"""文件夹插件执行服务。

包含两种插件运行时：

1. **声明式插件**（``kind='declarative'``）：插件的 ``config`` 字段是 JSON，
   描述了 action -> 匹配规则 -> 响应模板。引擎在文件夹下按规则找文件、按模
   板组装结果。安全可控，**支持任意客户端调用**。

2. **Python 代码插件**（``kind='python'``）：插件的 ``code`` 字段是 Python
   源码，必须定义 ``invoke(action: str, params: dict, ctx)`` 函数。引擎用
   ``exec`` 执行（带受限的 ``__builtins__``），将函数返回值直接作为响应。
   **任意代码执行风险**：只能让插件 owner（即 ``plugin.user_id``）自己调用，
   或通过 owner 的 API Key 调用。

二者都通过 :class:`PluginContext` 操作"文件夹下的文件"：

- :meth:`PluginContext.list_files`     列出所有文件（可控制是否递归）
- :meth:`PluginContext.find_file`      按 name/basename/ext/glob 精确或模式查找一条
- :meth:`PluginContext.find_files`     同上但返回列表
- :meth:`PluginContext.get_file_url`   返回 ``/api/external/download/<id>`` 完整 URL

设计声明式 config 示例（英文单词发音查询插件）::

    {
      "actions": {
        "lookup": {
          "params": [
            {"name": "word", "required": true, "transform": "lower"}
          ],
          "match": {
            "strategy": "filename",
            "patterns": ["{word}.mp3", "{word}-us.mp3", "{word}.wav"],
            "case_insensitive": true,
            "recursive": true
          },
          "response": {
            "success": true,
            "word": "{word}",
            "file_id": "{file.id}",
            "name": "{file.name}",
            "url": "{file.url}",
            "size": "{file.size}"
          },
          "not_found": {
            "success": false,
            "message": "word not found: {word}"
          }
        }
      }
    }
"""
from __future__ import annotations

import fnmatch
import hashlib
import hmac
import logging
import os
from datetime import datetime
from typing import Any, Iterable, List, Optional

from flask import current_app, has_app_context

from app import db
from app.models import File, FolderPlugin, User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 公开下载 URL 的签名工具
# ---------------------------------------------------------------------------
#
# 当 plugin.public=True 时, PluginContext.get_file_url 返回:
#   <base_url>/api/public/download/<file_id>?p=<plugin_id>&sig=<hex16>
#
# sig = HMAC_SHA256(key, msg)[:16] (hex)
#   key  = (user.api_key or 'NO_API_KEY') + ':' + str(plugin.id)
#   msg  = f'{plugin.id}:{file_id}'
#
# 设计点:
# - 用 user.api_key 作密钥, 不需要额外存秘密; 用户重置 API Key 时旧 URL 失效
# - 仅 16 字符 (64-bit) 截断, 防爆破足够 (2^64 量级), URL 也短
# - 不带过期: 公开插件的文件本就是"长期对外", 不引入时间复杂度
# - 仅在 plugin.public=True 时生成与校验, 关掉开关后所有旧 URL 立刻 403
# ---------------------------------------------------------------------------

_PUBLIC_SIG_BYTES = 8  # 16 hex chars


def _plugin_secret(plugin: FolderPlugin) -> bytes:
    """生成插件级的签名密钥。

    取用户当前 api_key 作为基底, 拼上 plugin.id 做隔离 —— 同一用户的不同插件
    互不能伪造对方的链接。
    """
    user = User.query.get(plugin.user_id)
    api_key = (user.api_key if user else None) or 'NO_API_KEY'
    return f'{api_key}:{plugin.id}'.encode('utf-8')


def sign_public_file(plugin: FolderPlugin, file_id: int) -> str:
    """返回 16 hex 字符的签名 (绑定 plugin_id + file_id)。"""
    mac = hmac.new(
        _plugin_secret(plugin),
        f'{plugin.id}:{int(file_id)}'.encode('utf-8'),
        hashlib.sha256,
    )
    return mac.hexdigest()[: _PUBLIC_SIG_BYTES * 2]


def verify_public_file(plugin: FolderPlugin, file_id: int, sig: str) -> bool:
    """常量时间比较签名, 防时序攻击。"""
    if not sig or not plugin or not plugin.public:
        return False
    expected = sign_public_file(plugin, int(file_id))
    return hmac.compare_digest(expected, sig)


# ---------------------------------------------------------------------------
# Plugin runtime context
# ---------------------------------------------------------------------------

class PluginContext:
    """插件可用的"工具箱"。

    设计原则：暴露足够多的查询能力，但**不允许写库**——插件是只读消费方，写
    操作必须通过正常的 file_manager API 走（这样能保证 webhook、版本管理等
    机制都生效）。
    """

    def __init__(
        self,
        plugin: FolderPlugin,
        folder: File,
        *,
        base_url: str,
        api_key: Optional[str] = None,
        public: bool = False,
    ) -> None:
        self.plugin = plugin
        self.folder = folder
        self.base_url = (base_url or '').rstrip('/')
        self.api_key = api_key
        # 是否走"公开"通道: 影响 get_file_url 输出格式。仅在 plugin.public=True
        # 且调用方走 /api/public/... 入口时才为 True。
        self.public = bool(public) and bool(getattr(plugin, 'public', False))
        # 给用户代码暴露的"只读字典视图"
        self.folder_info = {
            'id': folder.id,
            'name': folder.name,
            'path': folder.path,
            'user_id': folder.user_id,
        }

    # ----- 文件查询 -----

    def _path_prefix(self) -> str:
        p = (self.folder.path or '').strip('/')
        return f'{p}/' if p else ''

    def _query_files(self, recursive: bool) -> List[File]:
        q = File.query.filter_by(user_id=self.folder.user_id, is_directory=False)
        if recursive:
            prefix = self._path_prefix()
            if prefix:
                # path 形如 "X/Y/foo.mp3", 用 startswith 收子树
                q = q.filter(File.path.startswith(prefix))
            # root (prefix == '') 时不加约束, 等价于"用户所有文件"
        else:
            q = q.filter_by(parent_id=self.folder.id)
        return q.all()

    def list_files(self, *, recursive: Optional[bool] = None) -> List[dict]:
        rec = self.plugin.include_subdirs if recursive is None else bool(recursive)
        return [self._as_file_dict(f) for f in self._query_files(rec)]

    def find_file(
        self,
        *,
        name: Optional[str] = None,
        basename: Optional[str] = None,
        ext: Optional[str] = None,
        glob: Optional[str] = None,
        recursive: Optional[bool] = None,
        case_insensitive: bool = True,
    ) -> Optional[dict]:
        for f in self.find_files(
            name=name, basename=basename, ext=ext, glob=glob,
            recursive=recursive, case_insensitive=case_insensitive,
        ):
            return f
        return None

    def find_files(
        self,
        *,
        name: Optional[str] = None,
        basename: Optional[str] = None,
        ext: Optional[str] = None,
        glob: Optional[str] = None,
        recursive: Optional[bool] = None,
        case_insensitive: bool = True,
    ) -> List[dict]:
        rec = self.plugin.include_subdirs if recursive is None else bool(recursive)
        norm = (lambda s: s.lower()) if case_insensitive else (lambda s: s)

        def matches(f: File) -> bool:
            fname = f.name or ''
            if name is not None and norm(fname) != norm(name):
                return False
            if basename is not None:
                stem, _ = os.path.splitext(fname)
                if norm(stem) != norm(basename):
                    return False
            if ext is not None:
                e = ext.lstrip('.')
                if not norm(fname).endswith('.' + norm(e)):
                    return False
            if glob is not None and not fnmatch.fnmatch(norm(fname), norm(glob)):
                return False
            return True

        return [self._as_file_dict(f) for f in self._query_files(rec) if matches(f)]

    # ----- URL 生成 -----

    def get_file_url(self, file_id: int) -> str:
        """返回该文件的下载 URL。

        - ``self.public=False`` (默认): 返回 ``/api/external/download/<id>``，
          外部客户端需要带 ``X-API-Key`` 头才能下载。
        - ``self.public=True`` (插件已开 public 开关且通过 ``/api/public/...``
          调用): 返回 ``/api/public/download/<id>?p=<plugin_id>&sig=<hex16>``，
          浏览器/任意客户端直接 GET 即可, 无需鉴权。签名仅对该 plugin+file 组
          合有效, 关闭 public 或重置 API Key 后所有旧 URL 立刻失效。
        """
        fid = int(file_id)
        if self.public:
            sig = sign_public_file(self.plugin, fid)
            return f'{self.base_url}/api/public/download/{fid}?p={self.plugin.id}&sig={sig}'
        return f'{self.base_url}/api/external/download/{fid}'

    # ----- 日志 -----

    def log(self, msg: Any) -> None:
        logger.info('[plugin %s/%s] %s', self.plugin.folder_id, self.plugin.name, msg)

    # ----- 内部辅助 -----

    def _as_file_dict(self, f: File) -> dict:
        return {
            'id': f.id,
            'name': f.name,
            'path': f.path,
            'size': f.size,
            'file_type': f.file_type,
            'parent_id': f.parent_id,
            'is_directory': bool(f.is_directory),
            'url': self.get_file_url(f.id),
        }


# ---------------------------------------------------------------------------
# 调度入口
# ---------------------------------------------------------------------------

def invoke_plugin(
    plugin: FolderPlugin,
    action: str,
    params: Optional[dict],
    *,
    base_url: str,
    api_key: Optional[str] = None,
    public: bool = False,
) -> tuple[dict, int]:
    """运行一次插件 ``invoke``。

    Args:
        public: 调用方是否走 ``/api/public/...`` 无授权入口。该参数仅作用于
            ``PluginContext.get_file_url`` 的输出形式 —— 走 public 通道时返回
            带 HMAC 签名的公开 URL, 否则返回需要 API Key 的 external URL。

    返回 ``(response_dict, http_status_code)``。被调用方应当负责把
    ``response_dict`` 直接当 JSON 返回（响应里至少包含 ``success`` 字段）。
    """
    if not plugin.enabled:
        return {'success': False, 'error': 'plugin disabled'}, 403

    folder = File.query.get(plugin.folder_id)
    if not folder or folder.user_id != plugin.user_id or not folder.is_directory:
        return {'success': False, 'error': 'bound folder not found'}, 404

    ctx = PluginContext(plugin, folder, base_url=base_url, api_key=api_key, public=public)
    params = dict(params or {})

    try:
        if plugin.kind == 'declarative':
            result = _invoke_declarative(plugin, action, params, ctx)
        elif plugin.kind == 'python':
            result = _invoke_python(plugin, action, params, ctx)
        else:
            result = {'success': False, 'error': f'unknown plugin kind: {plugin.kind}'}
        ok = bool(result.get('success', False))
    except Exception as exc:  # noqa: BLE001
        logger.exception('plugin %s/%s invoke failed', plugin.folder_id, plugin.name)
        result = {'success': False, 'error': f'plugin error: {exc}'}
        ok = False

    # 更新统计（失败也算调用，便于排查）
    _record_invocation(plugin, ok=ok, error=None if ok else str(result.get('error', '')))

    status = 200 if ok else (result.pop('_status', 200) if isinstance(result, dict) else 200)
    return result, status


def _record_invocation(plugin: FolderPlugin, *, ok: bool, error: Optional[str]) -> None:
    try:
        plugin.invoke_count = (plugin.invoke_count or 0) + 1
        plugin.last_invoked_at = datetime.utcnow()
        plugin.last_status = 'ok' if ok else 'error'
        plugin.last_error = (error or '')[:512] if not ok else None
        db.session.commit()
    except Exception:  # noqa: BLE001
        if has_app_context():
            current_app.logger.exception('failed to record plugin invocation stats')
        db.session.rollback()


# ---------------------------------------------------------------------------
# 声明式引擎
# ---------------------------------------------------------------------------

_PARAM_TRANSFORMS = {
    'lower': lambda v: v.lower() if isinstance(v, str) else v,
    'upper': lambda v: v.upper() if isinstance(v, str) else v,
    'strip': lambda v: v.strip() if isinstance(v, str) else v,
}


def _resolve_params(action_def: dict, params: dict) -> tuple[dict, Optional[str]]:
    """按 action 定义验证/规整入参。返回 (resolved, error)。"""
    decls = action_def.get('params') or []
    out: dict = {}
    for spec in decls:
        if not isinstance(spec, dict):
            return {}, f'invalid param spec: {spec}'
        pname = spec.get('name')
        if not pname:
            return {}, 'param missing name'
        val = params.get(pname)
        if val is None or val == '':
            if spec.get('required'):
                return {}, f'missing required param: {pname}'
            val = spec.get('default')
        tname = spec.get('transform')
        if tname and val is not None:
            fn = _PARAM_TRANSFORMS.get(tname)
            if fn:
                val = fn(val)
        out[pname] = val
    # 透传未声明的额外字段，让模板里也能引用
    for k, v in params.items():
        out.setdefault(k, v)
    return out, None


def _format_template(value: Any, params: dict, file_dict: Optional[dict]) -> Any:
    """模板字符串里把 ``{key}`` / ``{file.attr}`` 替换为实际值。

    - ``{key}`` -> params[key]
    - ``{file.attr}`` -> file_dict[attr]（file_dict 为 None 时该占位保留 None）
    - 整个字符串就是 ``{file.id}`` / 单一占位时，**保留原始类型**（int/None）
    - dict / list 递归处理
    """
    if isinstance(value, dict):
        return {k: _format_template(v, params, file_dict) for k, v in value.items()}
    if isinstance(value, list):
        return [_format_template(v, params, file_dict) for v in value]
    if not isinstance(value, str):
        return value
    s = value.strip()
    # 整串占位时, 保留原类型
    if s.startswith('{') and s.endswith('}') and s.count('{') == 1:
        key = s[1:-1]
        return _lookup_placeholder(key, params, file_dict)
    # 嵌入式占位: 拼字符串
    out, i = [], 0
    while i < len(value):
        lb = value.find('{', i)
        if lb < 0:
            out.append(value[i:])
            break
        rb = value.find('}', lb)
        if rb < 0:
            out.append(value[i:])
            break
        out.append(value[i:lb])
        key = value[lb + 1:rb]
        rep = _lookup_placeholder(key, params, file_dict)
        out.append('' if rep is None else str(rep))
        i = rb + 1
    return ''.join(out)


def _lookup_placeholder(key: str, params: dict, file_dict: Optional[dict]) -> Any:
    key = key.strip()
    if key.startswith('file.'):
        if not file_dict:
            return None
        attr = key[5:]
        return file_dict.get(attr)
    return params.get(key)


def _invoke_declarative(plugin: FolderPlugin, action: str, params: dict, ctx: PluginContext) -> dict:
    cfg = plugin.parsed_config()
    actions_cfg = cfg.get('actions') or {}
    action_def = actions_cfg.get(action)
    if not action_def:
        return {
            'success': False,
            'error': f'unknown action: {action!r}',
            'available_actions': list(actions_cfg.keys()),
            '_status': 400,
        }

    resolved, perr = _resolve_params(action_def, params)
    if perr:
        return {'success': False, 'error': perr, '_status': 400}

    match_cfg = action_def.get('match') or {}
    strategy = (match_cfg.get('strategy') or 'filename').lower()
    recursive = bool(match_cfg.get('recursive', ctx.plugin.include_subdirs))
    case_ins = bool(match_cfg.get('case_insensitive', True))

    hit: Optional[dict] = None

    if strategy == 'filename':
        patterns: List[str] = []
        if match_cfg.get('pattern'):
            patterns.append(match_cfg['pattern'])
        patterns.extend(match_cfg.get('patterns') or [])
        if not patterns:
            return {'success': False, 'error': 'match.patterns is required', '_status': 400}

        files = ctx.list_files(recursive=recursive)
        norm = (lambda s: s.lower()) if case_ins else (lambda s: s)
        for pat in patterns:
            target = norm(_format_template(pat, resolved, None) or '')
            for f in files:
                if norm(f['name']) == target:
                    hit = f
                    break
            if hit:
                break

    elif strategy == 'glob':
        patterns = list(match_cfg.get('patterns') or ([match_cfg['pattern']] if match_cfg.get('pattern') else []))
        if not patterns:
            return {'success': False, 'error': 'match.patterns is required', '_status': 400}
        files = ctx.list_files(recursive=recursive)
        norm = (lambda s: s.lower()) if case_ins else (lambda s: s)
        for pat in patterns:
            target = norm(_format_template(pat, resolved, None) or '')
            for f in files:
                if fnmatch.fnmatch(norm(f['name']), target):
                    hit = f
                    break
            if hit:
                break

    elif strategy == 'list':
        # 不查找单条, 直接列文件夹下所有文件作为响应一部分
        files = ctx.list_files(recursive=recursive)
        tpl = action_def.get('response') or {}
        body = _format_template(tpl, resolved, None)
        if isinstance(body, dict):
            body.setdefault('success', True)
            body.setdefault('count', len(files))
            body.setdefault('files', files)
        return body if isinstance(body, dict) else {'success': True, 'files': files}

    else:
        return {'success': False, 'error': f'unsupported match strategy: {strategy}', '_status': 400}

    if hit:
        tpl = action_def.get('response') or {'success': True, 'file': '{file.url}'}
        body = _format_template(tpl, resolved, hit)
        if isinstance(body, dict):
            body.setdefault('success', True)
        return body if isinstance(body, dict) else {'success': True, 'value': body}

    tpl = action_def.get('not_found') or {'success': False, 'message': 'not found'}
    body = _format_template(tpl, resolved, None)
    if isinstance(body, dict):
        body.setdefault('success', False)
        body.setdefault('_status', 404)
    return body if isinstance(body, dict) else {'success': False, 'message': str(body), '_status': 404}


# ---------------------------------------------------------------------------
# Python 代码插件
# ---------------------------------------------------------------------------

# 受限的 builtins —— 屏蔽明显危险的入口；其它通过 `__import__` 仍可拿到, 因为
# 项目场景需要插件能 `import re`、`os.path` 等。这是**信任模型**，不是真沙箱。
_SAFE_BUILTIN_NAMES = (
    '__import__', 'abs', 'all', 'any', 'bool', 'bytearray', 'bytes', 'callable',
    'chr', 'dict', 'divmod', 'enumerate', 'filter', 'float', 'format',
    'frozenset', 'getattr', 'hasattr', 'hash', 'hex', 'id', 'int',
    'isinstance', 'issubclass', 'iter', 'len', 'list', 'map', 'max', 'min',
    'next', 'object', 'oct', 'ord', 'pow', 'print', 'range', 'repr', 'reversed',
    'round', 'set', 'setattr', 'slice', 'sorted', 'str', 'sum', 'tuple', 'type',
    'zip',
    # 异常族
    'Exception', 'ValueError', 'TypeError', 'KeyError', 'IndexError',
    'AttributeError', 'RuntimeError', 'StopIteration',
    # 常量
    'True', 'False', 'None',
)


def _build_safe_builtins() -> dict:
    import builtins as _bi
    out = {}
    for name in _SAFE_BUILTIN_NAMES:
        if hasattr(_bi, name):
            out[name] = getattr(_bi, name)
    return out


_SAFE_BUILTINS = _build_safe_builtins()


def _invoke_python(plugin: FolderPlugin, action: str, params: dict, ctx: PluginContext) -> dict:
    code = (plugin.code or '').strip()
    if not code:
        return {'success': False, 'error': 'plugin code is empty', '_status': 400}

    namespace: dict = {
        '__builtins__': _SAFE_BUILTINS,
        '__name__': f'folder_plugin_{plugin.id}',
    }

    try:
        compiled = compile(code, f'<plugin {plugin.id}>', 'exec')
        exec(compiled, namespace)  # noqa: S102 — 信任模型, 仅 owner 可调用
    except SyntaxError as e:
        return {'success': False, 'error': f'plugin syntax error: {e}', '_status': 400}
    except Exception as e:  # noqa: BLE001
        return {'success': False, 'error': f'plugin load failed: {e}', '_status': 500}

    fn = namespace.get('invoke')
    if not callable(fn):
        return {
            'success': False,
            'error': "plugin must define `def invoke(action, params, ctx)`",
            '_status': 400,
        }

    try:
        result = fn(action, params, ctx)
    except Exception as e:  # noqa: BLE001
        logger.exception('python plugin runtime error')
        return {'success': False, 'error': f'plugin runtime error: {e}', '_status': 500}

    if isinstance(result, dict):
        return result
    if result is None:
        return {'success': True}
    return {'success': True, 'value': result}


# ---------------------------------------------------------------------------
# 创建/更新时的预校验
# ---------------------------------------------------------------------------

def validate_plugin_spec(
    *,
    kind: str,
    config_json: Optional[str],
    code: Optional[str],
) -> List[str]:
    """返回 errors 列表（空 = 合法）。"""
    errs: List[str] = []
    if kind not in ('declarative', 'python'):
        errs.append(f'kind 必须是 declarative 或 python, 收到 {kind!r}')
        return errs

    if kind == 'declarative':
        if not config_json or not config_json.strip():
            errs.append('declarative 插件需要 config (JSON)')
            return errs
        import json as _json
        try:
            cfg = _json.loads(config_json)
        except _json.JSONDecodeError as e:
            errs.append(f'config 非合法 JSON: {e}')
            return errs
        if not isinstance(cfg, dict):
            errs.append('config 必须是 JSON 对象')
            return errs
        actions = cfg.get('actions')
        if not isinstance(actions, dict) or not actions:
            errs.append('config.actions 必须是非空对象')
        else:
            for aname, adef in actions.items():
                if not isinstance(adef, dict):
                    errs.append(f'actions.{aname} 必须是对象')
                    continue
                match = adef.get('match')
                if not isinstance(match, dict):
                    errs.append(f'actions.{aname}.match 必须是对象')

    if kind == 'python':
        if not code or not code.strip():
            errs.append('python 插件需要 code (Python 源码)')
            return errs
        try:
            compile(code, '<plugin>', 'exec')
        except SyntaxError as e:
            errs.append(f'code 编译失败: {e}')

    return errs
