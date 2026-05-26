"use strict";

/**
 * MusicFree 插件 - 对接 musicfree_server (file_manager 的桥接服务)
 * --------------------------------------------------------------
 * 与仓库里 file_manager 那份 `static/musicfree/file-manager.js` 的区别:
 *   - 那份是直连 file_manager, 鉴权用 X-API-Key, 用户必须自己拿 API Key
 *   - 这份是经 musicfree_server 中转, 鉴权用 X-Music-Token (可不开)
 *
 * 鉴权与 file_manager 拉流的关系 (重点):
 *   musicfree_server 自己不存 file_manager 的 API Key (默认不需要).
 *   播放时是 MusicFree 客户端拿到 source URL 后, 直接带 X-API-Key 去拉.
 *   所以本插件提供一个 `apiKey` 用户变量, 让你把 file_manager 的 API Key
 *   填进来. 插件会在调用 source / lyric 时通过 `X-File-Manager-Key` 头
 *   传给 musicfree_server, 它再原样塞进 source 响应里的 headers, 由
 *   MusicFree 客户端使用. (服务端配 MFS_FILE_MANAGER_API_KEY 也可以,
 *   优先级: 客户端填的 > 服务端配的)
 *
 * 安装:
 *   1. 在 MusicFree 「设置 -> 插件管理 -> 从网络安装」填:
 *        __MFS_SRC_URL__
 *      这个地址由本服务在响应时按 MFS_PUBLIC_BASE_URL 自动生成,
 *      所以装好后会自动把 baseUrl 指到本服务, 无需再填.
 *   2. 在 file_manager 「用户中心 -> API Key」生成一个 key, 填到本插件
 *      用户变量 `apiKey` (这个 key 不会上传到 musicfree_server 的状态文件,
 *      只会在每次拉流时透传).
 *   3. 如果服务端开了 MFS_QUERY_TOKEN, 在「插件设置」里填 token 即可.
 *
 * Hermes 引擎限制提醒:
 *   - 不能用 `async () => {}` 箭头 async, 必须用 `async function () {}`
 *   - 不能依赖 Node 原生模块; 内置 fetch / axios 可用
 */

var PLATFORM = "FileManager (via musicfree_server)";
var PLUGIN_VERSION = "0.1.0";

// 这两个占位符在 musicfree_server `GET /static/musicfree/file-manager.js`
// 的响应处理里会被替换成当前服务的 public_base_url, 让插件开箱即用。
var DEFAULT_BASE_URL = "__MFS_PUBLIC_BASE_URL__";
var DEFAULT_SRC_URL = "__MFS_SRC_URL__";

// ---------------------------------------------------------------
// 用户变量 / 工具函数
// ---------------------------------------------------------------

function _getEnv() {
    if (typeof env !== "undefined" && env && typeof env.getUserVariables === "function") {
        return env.getUserVariables() || {};
    }
    if (typeof getUserVariables === "function") {
        return getUserVariables() || {};
    }
    return {};
}

function _getBaseUrl() {
    var vars = _getEnv();
    var raw = (vars.baseUrl || DEFAULT_BASE_URL || "").trim();
    if (!raw) {
        throw new Error("请在插件设置里填写 baseUrl (musicfree_server 的对外地址)");
    }
    return raw.replace(/\/+$/, "");
}

function _getToken() {
    var vars = _getEnv();
    return (vars.token || "").trim();
}

function _getFileManagerApiKey() {
    var vars = _getEnv();
    return (vars.apiKey || "").trim();
}

/**
 * 统一封装一次 GET 请求, 自动带:
 *   - X-Music-Token  (本服务自己的查询 token, 如果配置了)
 *   - X-File-Manager-Key  (file_manager API Key, 如果填了 apiKey 用户变量)
 * extraHeaders 是调用方临时附加的 header (一般不用).
 */
async function _apiGet(path, extraHeaders) {
    var url = _getBaseUrl() + path;
    var headers = { Accept: "application/json" };
    var token = _getToken();
    if (token) {
        headers["X-Music-Token"] = token;
    }
    var fmKey = _getFileManagerApiKey();
    if (fmKey) {
        headers["X-File-Manager-Key"] = fmKey;
    }
    if (extraHeaders) {
        for (var k in extraHeaders) {
            if (Object.prototype.hasOwnProperty.call(extraHeaders, k)) {
                headers[k] = extraHeaders[k];
            }
        }
    }
    var resp = await fetch(url, { method: "GET", headers: headers });
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

    // 用户可以把这个 URL 填到 MusicFree 的「从网络安装」, 走自动更新。
    srcUrl: DEFAULT_SRC_URL,

    // 内容会随 file_manager webhook 变化, 不要缓存。
    cacheControl: "no-store",

    userVariables: [
        {
            key: "baseUrl",
            name: "服务地址 (baseUrl)",
            hint: "musicfree_server 的对外地址, 例如 http://1.2.3.4:10006; 留空则用插件下载时绑定的地址",
        },
        {
            key: "apiKey",
            name: "file_manager API Key",
            hint: "在 file_manager 用户中心生成. 用来拉播放流, 通过 X-File-Manager-Key 透传给服务. 若服务端已配 MFS_FILE_MANAGER_API_KEY 可留空",
        },
        {
            key: "token",
            name: "Token (可选)",
            hint: "服务端如果开了 MFS_QUERY_TOKEN, 在这里填; 否则留空即可",
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
     * 调 musicfree_server `/api/music/track/<id>/source`, 它返回
     * ``{url, headers}``:
     *   - ``url``: 指向 file_manager 的 ``/api/external/download/<id>``
     *     (或更通用的 stream 端点)
     *   - ``headers.X-API-Key``: 优先用本插件 ``apiKey`` 变量里填的, 通过
     *     ``X-File-Manager-Key`` 头透传到服务端再原样塞回; 客户端没填且
     *     服务端也没配时为空, MusicFree 拉流时会 401, 这时 ``_apiGet`` 会
     *     抛错或返回 ``_warning`` 字段.
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
            // 后端拒绝 (403/404) 或网络异常 → 返 null, 让 MusicFree 自己报错
        }
        return null;
    },

    /** 取歌词: musicfree_server 已在内部根据同目录同名 .lrc 配对好。 */
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
            // 没歌词不算错
        }
        return null;
    },

    /** 推荐歌单: 每个含音频的目录被当作一个"歌单"。 */
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
