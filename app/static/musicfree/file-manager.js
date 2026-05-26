"use strict";

/**
 * MusicFree 插件 - 对接自托管 file_manager
 * --------------------------------------------------------------
 * 把 file_manager 中所有 audio 类型文件 (mp3/wav/ogg/flac/aac/m4a) 当作
 * 音源, 接入到 MusicFree Android / Desktop 客户端。
 *
 * 用法:
 *   1. 在 MusicFree 「设置 -> 插件管理」中, 用 URL 安装本插件 (或下载 js 后本地安装)
 *      默认 URL:  http://<你的 file_manager 地址>/static/musicfree/file-manager.js
 *   2. 安装后在「插件设置 - 用户变量」里填:
 *        baseUrl   file_manager 服务地址, 如 http://192.168.1.100:5000
 *        apiKey    在 file_manager 用户中心生成的 API Key
 *   3. 即可在 MusicFree 里搜索、浏览、播放 file_manager 中的音频
 *
 * 协议参考:
 *   https://musicfree.catcat.work/plugin/introduction.html
 *
 * 注意 (Hermes 引擎限制):
 *   - 不能用 `async () => {}` 箭头 async, 必须用 `async function () {}`
 *   - 不能依赖 Node 原生模块; 内置 fetch / axios 可用
 */

var PLATFORM = "FileManager";
var PLUGIN_VERSION = "0.1.0";

// ---------------------------------------------------------------
// 用户变量 / 工具函数
// ---------------------------------------------------------------

function _getEnv() {
    // MusicFree 注入了一个全局 `env`, env.getUserVariables() 返回插件用户变量
    if (typeof env !== "undefined" && env && typeof env.getUserVariables === "function") {
        return env.getUserVariables() || {};
    }
    // 兼容旧版/桌面端: 直接挂在 globalThis.getUserVariables
    if (typeof getUserVariables === "function") {
        return getUserVariables() || {};
    }
    return {};
}

function _getBaseUrl() {
    var vars = _getEnv();
    var raw = (vars.baseUrl || "").trim();
    if (!raw) {
        throw new Error("请在插件设置里填写 baseUrl (file_manager 服务地址)");
    }
    return raw.replace(/\/+$/, "");
}

function _getApiKey() {
    var vars = _getEnv();
    var key = (vars.apiKey || "").trim();
    if (!key) {
        throw new Error("请在插件设置里填写 apiKey (file_manager API Key)");
    }
    return key;
}

/** 统一封装一次 GET 请求, 自动带 X-API-Key 头, 解析 JSON。 */
async function _apiGet(path) {
    var url = _getBaseUrl() + path;
    var resp = await fetch(url, {
        method: "GET",
        headers: {
            "X-API-Key": _getApiKey(),
            "Accept": "application/json",
        },
    });
    if (!resp.ok) {
        var msg = "HTTP " + resp.status;
        try {
            var body = await resp.json();
            if (body && body.error) {
                msg = body.error;
            }
        } catch (_) {
            // ignore
        }
        throw new Error(msg);
    }
    return await resp.json();
}

// ---------------------------------------------------------------
// 主插件实例
// ---------------------------------------------------------------

module.exports = {
    platform: PLATFORM,
    version: PLUGIN_VERSION,
    author: "yiye",
    appVersion: ">=0.1.0",

    // 这个 URL 用户可以填到 MusicFree 的"从网络安装"里, 实现一键安装+自动更新。
    // 用户安装时, MusicFree 会把这个 URL 记下来, 后续点"更新"时重新拉。
    srcUrl: "http://<host>:<port>/static/musicfree/file-manager.js",

    // file_manager 内容会随时变, 不缓存 (避免播放时拿到已删除文件的旧链接)
    cacheControl: "no-store",

    /** MusicFree 会展示这两个字段, 让用户填写。key 用来在代码里 getUserVariables 取值。 */
    userVariables: [
        {
            key: "baseUrl",
            name: "服务地址 (baseUrl)",
            hint: "如 http://192.168.1.100:5000 (不要带末尾斜杠)",
        },
        {
            key: "apiKey",
            name: "API Key",
            hint: "在 file_manager 用户中心生成, 用来鉴权",
        },
    ],

    supportedSearchType: ["music", "sheet"],

    /**
     * 搜索接口
     * @param {string} query
     * @param {number} page
     * @param {"music"|"sheet"} type
     */
    async search(query, page, type) {
        var t = type || "music";
        var url = "/api/music/search?q=" + encodeURIComponent(query || "") +
                  "&page=" + (page || 1) +
                  "&type=" + encodeURIComponent(t);
        var data = await _apiGet(url);
        return {
            isEnd: !!data.isEnd,
            data: data.data || [],
        };
    },

    /**
     * 获取音源 URL
     *
     * 让后端 ``/api/music/track/<id>/source`` 来决定真正的 URL + headers ——
     * 这样后端可以在返回前先校验:
     *   1) 用户 ``musicfree_enabled`` 总开关是否打开
     *   2) 该曲目所在目录是否在 ``music_shared`` 白名单内
     * 任一不满足都会拒绝, 防止越权拉流。
     *
     * 实际拿到的 ``url`` 仍然指向 ``/api/external/download/<id>``,
     * 配合 ``headers.X-API-Key``, ExoPlayer / mpv 会带头直接拉流。
     */
    async getMediaSource(musicItem, quality) {
        var fileId = musicItem._file_id || musicItem.id;
        if (!fileId) {
            return null;
        }
        try {
            var data = await _apiGet("/api/music/track/" + fileId + "/source");
            if (data && data.url) {
                return { url: data.url, headers: data.headers || {} };
            }
        } catch (e) {
            // 后端拒绝(403/404) 或网络异常 → fallback 到本地拼 URL, 让 MusicFree 直接报错
        }
        return null;
    },

    /** 取歌词: 找同目录同名 .lrc 文件 */
    async getLyric(musicItem) {
        var fileId = musicItem._file_id || musicItem.id;
        if (!fileId) {
            return null;
        }
        try {
            var data = await _apiGet("/api/music/track/" + fileId + "/lyric");
            if (data && data.rawLrc) {
                return { rawLrc: data.rawLrc };
            }
        } catch (e) {
            // 没歌词不算错, 直接 fallback
        }
        return null;
    },

    /** 推荐歌单: 我们把每个含音频的目录当作一个"歌单" */
    async getRecommendSheetTags() {
        return {
            pinned: [],
            data: [
                {
                    title: "全部",
                    data: [{ id: "all", title: "全部音乐目录" }],
                },
            ],
        };
    },

    async getRecommendSheetsByTag(tag, page) {
        var data = await _apiGet("/api/music/sheets?page=" + (page || 1));
        return {
            isEnd: !!data.isEnd,
            data: data.data || [],
        };
    },

    /** 歌单详情 */
    async getMusicSheetInfo(sheet, page) {
        var sid = sheet._folder_id || sheet.id;
        if (!sid) {
            return { isEnd: true, musicList: [] };
        }
        var data = await _apiGet("/api/music/sheet/" + sid + "?page=" + (page || 1));
        return {
            isEnd: !!data.isEnd,
            musicList: data.musicList || [],
            sheetItem: data.sheet,
        };
    },
};
