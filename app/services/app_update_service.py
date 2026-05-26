"""
应用更新检测：按包名 + 渠道维度匹配版本记录，与客户端版本比较，生成下载链接。
"""
import json
import re
from typing import Any, Dict, Optional, Tuple

from app.models.version_model import FileVersion


def _parse_extra(row: FileVersion) -> Dict[str, Any]:
    if not row.extra_fields:
        return {}
    try:
        return json.loads(row.extra_fields)
    except (json.JSONDecodeError, TypeError):
        return {}


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _version_tuple(s: Any) -> Optional[Tuple[int, ...]]:
    if s is None:
        return None
    nums = [int(x) for x in re.findall(r'\d+', str(s))]
    return tuple(nums) if nums else None


def _cmp_numeric_tuples(a: Tuple[int, ...], b: Tuple[int, ...]) -> int:
    n = max(len(a), len(b))
    aa = a + (0,) * (n - len(a))
    bb = b + (0,) * (n - len(b))
    if aa > bb:
        return 1
    if aa < bb:
        return -1
    return 0


def compare_with_client(
    server: FileVersion,
    client_version_name: Optional[str],
    client_version_code: Any,
) -> Tuple[bool, str]:
    """
    判断服务端是否比客户端「更新」。
    优先使用 Android versionCode；否则用语义化版本号数字段比较。
    """
    server_extra = _parse_extra(server)
    cc = _to_int(client_version_code)
    sc = _to_int(server_extra.get('android_version_code'))
    if sc is None and server.version and str(server.version).isdigit():
        sc = _to_int(server.version)

    has_client = cc is not None or (client_version_name and str(client_version_name).strip())

    if not has_client:
        return True, 'no_client_version'

    if cc is not None and sc is not None:
        return sc > cc, 'version_code'

    sv = (server_extra.get('android_version_name') or server.version or '').strip()
    cv = (client_version_name or '').strip()
    if not sv or not cv:
        if cc is not None and sc is None:
            return False, 'server_missing_version_code'
        return False, 'incomparable'

    ta, tb = _version_tuple(sv), _version_tuple(cv)
    if ta is not None and tb is not None:
        c = _cmp_numeric_tuples(ta, tb)
        return c > 0, 'semver'

    return sv != cv and sv > cv, 'string'


def find_latest_for_app(
    package: Optional[str],
    system: Optional[str],
    type_: Optional[str],
    vendor: Optional[str],
    device_type: Optional[str],
) -> Optional[FileVersion]:
    """
    查找该应用/渠道下最新一条有效版本（按入库时间倒序），
    若提供 package 则 extra_fields.android_package 须一致。
    """
    q = FileVersion.query.filter(FileVersion.status == 'active')
    if system:
        q = q.filter(FileVersion.system == system)
    if type_:
        q = q.filter(FileVersion.type == type_)
    if vendor:
        q = q.filter(FileVersion.vendor == vendor)
    if device_type:
        q = q.filter(FileVersion.device_type == device_type)

    rows = q.order_by(FileVersion.timestamp.desc()).all()
    pk = (package or '').strip()
    if pk:
        for v in rows:
            ex = _parse_extra(v)
            if ex.get('android_package') == pk:
                return v
        return None

    if not (system and type_ and vendor and device_type):
        return None
    return rows[0] if rows else None


def build_download_url(version: FileVersion) -> str:
    from flask import request
    from urllib.parse import quote

    base = f"{request.scheme}://{request.host}"
    path = version.file_path or ''
    uid = version.user_id if version.user_id is not None else ''
    ver = version.version or ''
    return (
        f"{base}/api/external/download/{quote(path, safe='/')}"
        f"?user_id={uid}&version={quote(str(ver), safe='')}"
    )


def latest_to_payload(version: FileVersion) -> Dict[str, Any]:
    extra = _parse_extra(version)
    return {
        'id': version.id,
        'file_id': version.file_id,
        'version': version.version,
        'android_version_name': extra.get('android_version_name'),
        'android_version_code': extra.get('android_version_code'),
        'android_package': extra.get('android_package'),
        'system': version.system,
        'type': version.type,
        'vendor': version.vendor,
        'device_type': version.device_type,
        'file_size': version.file_size,
        'checksum': version.checksum,
        'checksum_type': version.checksum_type,
        'file_path': version.file_path,
        'upgrade_desc': version.upgrade_desc,
        'version_desc': version.version_desc,
        'released_at': version.timestamp.strftime('%Y-%m-%d %H:%M:%S') if version.timestamp else None,
        'download_url': build_download_url(version),
    }
