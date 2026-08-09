"""Google Driveクライアント(T-110)。

- `DriveClient` は取込ワーカーが依存する最小プロトコル。テストはフェイク実装、
  実運用は `GoogleDriveClient`(google-api-python-client)を使う。
- フォルダID・OAuth認証情報は環境変数・設定ファイルで渡す(リポジトリへ
  コミットしない。SECURITY.md)。
- Drive上のファイルは読み取り(list/get/download)のみ。削除・移動・改名しない。
  書き込みは結果フォルダ(results/<job_id>/)配下への作成・アップロードのみ。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_CHUNK_SIZE = 8 * 1024 * 1024


@dataclass(frozen=True)
class DriveFileInfo:
    file_id: str
    name: str
    mime_type: str | None
    size_bytes: int | None
    modified_time: str | None


class DriveClient(Protocol):
    """取込ワーカーが必要とするDrive操作の最小集合。"""

    def list_files(self, folder_id: str) -> list[DriveFileInfo]: ...

    def get_file_info(self, file_id: str) -> DriveFileInfo: ...

    def download_file(self, file_id: str, dest: Path) -> None:
        """チャンク単位でdestへダウンロードする(全編をメモリへ読み込まない)。"""
        ...

    def ensure_folder(self, parent_id: str, name: str) -> str:
        """親フォルダ配下に名前nameのフォルダを(なければ作成して)返す。"""
        ...

    def upload_file(self, folder_id: str, source: Path, name: str) -> str:
        """フォルダ内へname名でアップロードする。**同名が既にあれば置換する**
        (冪等:同じジョブの再試行で結果ファイルが増殖しない)。"""
        ...


@dataclass(frozen=True)
class DriveIngestConfig:
    """取込ワーカーの設定。フォルダIDは .env で指定する(.env.example参照)。"""

    inbox_folder_id: str
    results_parent_folder_id: str  # 通常は受け箱直下の "results" フォルダの親=受け箱自身
    max_retries: int = 3
    # アップロード完了判定:サイズ・modifiedTimeがこの回数の連続確認で不変であること
    stability_confirmations: int = 2
    # 連続確認の最小時間間隔(秒)。間隔が足りない観測は確認回数に数えない
    # (連続実行しても数秒で2回確認扱いにならない=4時間動画の途中取得防止)
    stability_interval_seconds: int = 60

    @classmethod
    def load(cls) -> "DriveIngestConfig":
        inbox = os.environ.get("BIO_OBSERVER_DRIVE_INBOX_FOLDER_ID", "")
        if not inbox:
            raise ValueError("BIO_OBSERVER_DRIVE_INBOX_FOLDER_ID が未設定です(.env参照)")
        return cls(
            inbox_folder_id=inbox,
            results_parent_folder_id=os.environ.get(
                "BIO_OBSERVER_DRIVE_RESULTS_PARENT_FOLDER_ID", "") or inbox,
            max_retries=int(os.environ.get("BIO_OBSERVER_DRIVE_MAX_RETRIES", "3")),
            stability_confirmations=int(
                os.environ.get("BIO_OBSERVER_DRIVE_STABILITY_CONFIRMATIONS", "2")),
            stability_interval_seconds=int(
                os.environ.get("BIO_OBSERVER_DRIVE_STABILITY_INTERVAL_SECONDS", "60")),
        )


class GoogleDriveClient:
    """google-api-python-client による実装(実運用のWindows解析PC用)。

    認証:OAuthクライアント(credentials.json)+初回ブラウザ認可で token.json を
    生成・更新する。いずれもGit管理外のパスを環境変数で指定する:
      BIO_OBSERVER_DRIVE_CREDENTIALS_FILE / BIO_OBSERVER_DRIVE_TOKEN_FILE

    依存は optional-dependencies `drive`(pip install ".[drive]")。本クラスは
    ネットワーク・認証が必要なため自動テストではフェイク実装を使う(D-27)。
    """

    SCOPES = ["https://www.googleapis.com/auth/drive"]

    def __init__(self, credentials_file: str | None = None, token_file: str | None = None):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        credentials_file = credentials_file or os.environ["BIO_OBSERVER_DRIVE_CREDENTIALS_FILE"]
        token_file = token_file or os.environ["BIO_OBSERVER_DRIVE_TOKEN_FILE"]

        creds = None
        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, self.SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(credentials_file, self.SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_file, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        self._service = build("drive", "v3", credentials=creds)

    @staticmethod
    def _to_info(item: dict) -> DriveFileInfo:
        size = item.get("size")
        return DriveFileInfo(
            file_id=item["id"],
            name=item.get("name", ""),
            mime_type=item.get("mimeType"),
            size_bytes=int(size) if size is not None else None,
            modified_time=item.get("modifiedTime"),
        )

    def list_files(self, folder_id: str) -> list[DriveFileInfo]:
        files: list[DriveFileInfo] = []
        token = None
        while True:
            resp = self._service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
                pageToken=token,
            ).execute()
            files.extend(self._to_info(f) for f in resp.get("files", []))
            token = resp.get("nextPageToken")
            if not token:
                return files

    def get_file_info(self, file_id: str) -> DriveFileInfo:
        item = self._service.files().get(
            fileId=file_id, fields="id, name, mimeType, size, modifiedTime"
        ).execute()
        return self._to_info(item)

    def download_file(self, file_id: str, dest: Path) -> None:
        from googleapiclient.http import MediaIoBaseDownload

        request = self._service.files().get_media(fileId=file_id)
        with open(dest, "wb") as f:
            downloader = MediaIoBaseDownload(f, request, chunksize=_CHUNK_SIZE)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            f.flush()
            os.fsync(f.fileno())

    def ensure_folder(self, parent_id: str, name: str) -> str:
        resp = self._service.files().list(
            q=(f"'{parent_id}' in parents and name = '{name}' and trashed = false "
               "and mimeType = 'application/vnd.google-apps.folder'"),
            fields="files(id)",
        ).execute()
        existing = resp.get("files", [])
        if existing:
            return existing[0]["id"]
        created = self._service.files().create(
            body={"name": name, "parents": [parent_id],
                  "mimeType": "application/vnd.google-apps.folder"},
            fields="id",
        ).execute()
        return created["id"]

    def upload_file(self, folder_id: str, source: Path, name: str) -> str:
        """同名ファイルがあれば内容を置換(update)、なければ新規作成(冪等)。"""
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(source), resumable=True, chunksize=_CHUNK_SIZE)
        escaped = name.replace("'", "\\'")
        resp = self._service.files().list(
            q=f"'{folder_id}' in parents and name = '{escaped}' and trashed = false",
            fields="files(id)",
        ).execute()
        existing = resp.get("files", [])
        if existing:
            updated = self._service.files().update(
                fileId=existing[0]["id"], media_body=media, fields="id",
            ).execute()
            return updated["id"]
        created = self._service.files().create(
            body={"name": name, "parents": [folder_id]},
            media_body=media,
            fields="id",
        ).execute()
        return created["id"]
