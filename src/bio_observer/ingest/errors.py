"""取込ワーカーの例外分類(T-113 Codexレビュー対応)。

ワーカーは失敗の「原因」で扱いを変える。判定はモジュール名の一括判定ではなく、
**具体的な例外型と、Drive API 応答の HTTP ステータス+reason** で行う。

  transient   通信断・一時的なサービス障害。待てば直る:再試行回数を消費せず次サイクルで再開
  rate_limited Drive API のレート制限(403/429 + rateLimitExceeded 等)。制限解除後に再開
  auth        再認可・設定修正など人の対応が必要(認証失敗・証明書検証失敗・401)。
              ジョブの再試行回数を消費せず、ワーカーを停止して案内する
  permanent   上記以外(内部データ異常・権限不足・削除済み・ローカルI/Oエラー等)。
              通常の再試行→上限で failed

依存ライブラリ(google-auth / httplib2 / googleapiclient)は optional `drive` のため、
import できない環境では該当型は単に判定対象外になる(発生もしない)。
"""

from __future__ import annotations

import errno
import http.client
import json
import socket
import ssl

TRANSIENT = "transient"
RATE_LIMITED = "rate_limited"
AUTH = "auth"
PERMANENT = "permanent"

# 待機継続(再試行回数を消費しない)とする分類
WAITABLE = frozenset({TRANSIENT, RATE_LIMITED})

try:  # google-auth(optional)
    from google.auth import exceptions as _gauth
    _AUTH_EXCEPTIONS: tuple[type, ...] = (_gauth.RefreshError, _gauth.DefaultCredentialsError,
                                          _gauth.MalformedError, _gauth.OAuthError)
    _TRANSPORT_EXCEPTIONS: tuple[type, ...] = (_gauth.TransportError,)
    _GAUTH_BASE: tuple[type, ...] = (_gauth.GoogleAuthError,)
except ImportError:  # pragma: no cover - drive extra 未導入環境
    _AUTH_EXCEPTIONS = ()
    _TRANSPORT_EXCEPTIONS = ()
    _GAUTH_BASE = ()

try:  # httplib2(google-api-python-client の依存)
    from httplib2 import ServerNotFoundError as _ServerNotFoundError
    _DNS_EXCEPTIONS: tuple[type, ...] = (_ServerNotFoundError,)
except ImportError:  # pragma: no cover
    _DNS_EXCEPTIONS = ()

# OSError のうち通信断と見なす errno(ConnectionError/TimeoutError 以外で発生するもの)
_NETWORK_ERRNOS = frozenset(
    getattr(errno, name) for name in (
        "ENETDOWN", "ENETUNREACH", "ENETRESET", "ECONNABORTED", "ECONNRESET",
        "ECONNREFUSED", "ETIMEDOUT", "EHOSTDOWN", "EHOSTUNREACH", "EPIPE",
    ) if hasattr(errno, name)
)

# Drive API の 403 reason(Google公式「Resolve errors」に基づく)
_RATE_LIMIT_REASONS = frozenset({
    "rateLimitExceeded", "userRateLimitExceeded", "sharingRateLimitExceeded",
    "dailyLimitExceeded",          # 日次上限:時間経過で解除されるため待機扱い
    "RESOURCE_EXHAUSTED",          # error.status 形式
})
_AUTH_REASONS = frozenset({"authError", "UNAUTHENTICATED", "unauthorized"})


def _http_status(exc: BaseException) -> int | None:
    """googleapiclient.errors.HttpError 互換(resp.status)から HTTP ステータスを得る。"""
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status is None:
        return None
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def http_error_reasons(exc: BaseException) -> set[str]:
    """HttpError から reason を集める(error_details → content JSON → 文言中の既知トークン)。"""
    reasons: set[str] = set()
    try:
        details = getattr(exc, "error_details", None)  # HttpError のプロパティ(内部で content を解析)
    except Exception:  # noqa: BLE001 — 壊れた応答本文でも分類器は落とさない
        details = None
    if isinstance(details, list):
        for d in details:
            if isinstance(d, dict) and d.get("reason"):
                reasons.add(str(d["reason"]))
    content = getattr(exc, "content", None)
    if isinstance(content, (bytes, bytearray)):
        content = content.decode("utf-8", "replace")
    if isinstance(content, str) and content.strip():
        try:
            data = json.loads(content)
        except ValueError:
            data = None
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            entries = [x for key in ("errors", "details")
                       if isinstance(err.get(key), list) for x in err[key]]
            for e in entries:
                if isinstance(e, dict) and e.get("reason"):
                    reasons.add(str(e["reason"]))
            if isinstance(err.get("status"), str):
                reasons.add(err["status"])
    if not reasons:
        text = str(exc)
        for token in _RATE_LIMIT_REASONS | _AUTH_REASONS:
            if token in text:
                reasons.add(token)
    return reasons


def _classify_http(exc: BaseException, status: int) -> str:
    reasons = http_error_reasons(exc)
    if status == 401 or reasons & _AUTH_REASONS:
        return AUTH
    if status == 429 or (status == 403 and reasons & _RATE_LIMIT_REASONS):
        return RATE_LIMITED
    if status >= 500:
        return TRANSIENT
    # 400 / 403(権限不足・storageQuotaExceeded・cannotDownloadAbusiveFile 等)/ 404 / その他
    return PERMANENT


def classify_error(exc: BaseException) -> str:
    """例外を TRANSIENT / RATE_LIMITED / AUTH / PERMANENT のいずれかに分類する。"""
    # 1) Drive API 応答(HttpError 互換):ステータス+reason で判定
    status = _http_status(exc)
    if status is not None:
        return _classify_http(exc, status)
    # 2) 認証・設定(人の対応が必要)。ただし google-auth はトークンサーバ側の一時障害
    #    (500/503・temporarily_unavailable 等)も RefreshError(retryable=True) で返すため、
    #    retryable なものは通信断として待つ(再認可を誤案内してワーカーを止めない)
    if _AUTH_EXCEPTIONS and isinstance(exc, _AUTH_EXCEPTIONS):
        if getattr(exc, "retryable", False) is True:
            return TRANSIENT
        return AUTH
    if isinstance(exc, ssl.SSLCertVerificationError):
        return AUTH
    # 3) 通信断(具体的な型・errno)
    if _TRANSPORT_EXCEPTIONS and isinstance(exc, _TRANSPORT_EXCEPTIONS):
        return TRANSIENT
    if _DNS_EXCEPTIONS and isinstance(exc, _DNS_EXCEPTIONS):
        return TRANSIENT
    if isinstance(exc, (ConnectionError, TimeoutError, socket.gaierror, socket.herror,
                        ssl.SSLError, http.client.HTTPException)):
        return TRANSIENT
    if isinstance(exc, OSError) and exc.errno in _NETWORK_ERRNOS:
        return TRANSIENT
    # 4) その他の google-auth 例外(想定外の認証系)は人の対応が必要側に寄せる
    if _GAUTH_BASE and isinstance(exc, _GAUTH_BASE):
        return AUTH
    # ValueError / KeyError / json / sqlite / FileNotFoundError / PermissionError / ENOSPC 等
    return PERMANENT


CATEGORY_LABELS = {
    TRANSIENT: "通信エラー(一時的)",
    RATE_LIMITED: "Drive API レート制限",
    AUTH: "認証・設定エラー(人の対応が必要)",
    PERMANENT: "エラー",
}
