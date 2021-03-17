from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from aioaria2 import Aria2WebsocketTrigger
from async_property import async_property


def human_readable_bytes(
    value: int,
    digits: int = 2,
    delim: str = "",
    postfix: str = ""
) -> str:
    chosen_unit = "B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        if value > 1000:
            value /= 1024
            chosen_unit = unit
        else:
            break
    return f"{value:.{digits}f}" + delim + chosen_unit + postfix


class BitTorrent:

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data or {}

    def __str__(self):
        return self.info["name"]

    @property
    def announce_list(self) -> Optional[List[List[str]]]:
        return self._data.get("announceList")

    @property
    def comment(self) -> Optional[str]:
        return self._data.get("comment")

    @property
    def creation_date(self) -> datetime:
        return datetime.fromtimestamp(self._data["creationDate"])

    @property
    def mode(self) -> Optional[str]:
        return self._data.get("mode")

    @property
    def info(self) -> Optional[dict]:
        return self._data.get("info")


class File:

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data or {}

    def __str__(self):
        return str(self.path)

    def __eq__(self, other):
        return self.path == other.path

    @property
    def index(self) -> int:
        return int(self._data["index"])

    @property
    def path(self) -> Path:
        return Path(self._data["path"])

    @property
    def metadata(self) -> bool:
        return str(self.path).startswith("[METADATA]")

    @property
    def length(self) -> int:
        return int(self._data["length"])

    @property
    def completed_length(self) -> int:
        return int(self._data["completedLength"])

    @property
    def uris(self) -> Optional[List[str]]:
        return self._data.get("uris")


class Download:

    def __init__(
        self,
        client: Aria2WebsocketTrigger,
        data: Dict[str, Any]
    ) -> None:
        self.client = client
        self._data = data or {}

        self._name = ""
        self._files: List[File] = []
        self._bittorrent = None

    def __str__(self):
        return self.name

    def __eq__(self, other):
        return self.gid == other.gid

    @async_property
    async def update(self) -> "Download":
        self._data = await self.client.tellStatus(self.gid)

        self._name = ""
        self._files = []
        self._bittorrent = None

        return self

    @property
    def name(self) -> str:
        if not self._name:
            if self.bittorrent and self.bittorrent.info:
                self._name = self.bittorrent.info["name"]
            elif self.files[0].metadata:
                self._name = str(self.files[0].path)
            else:
                file_path = str(self.files[0].path.absolute())
                dir_path = str(self.dir.absolute())
                if file_path.startswith(dir_path):
                    start_pos = len(dir_path) + 1
                    self._name = Path(file_path[start_pos:]).parts[0]
                else:
                    try:
                        self._name = self.files[0].uris[0]["uri"].split("/")[-1]
                    except IndexError:
                        pass
        return self._name

    @property
    def gid(self) -> str:
        return self._data["gid"]

    @property
    def status(self) -> str:
        return self._data["status"]

    @property
    def active(self) -> bool:
        return self.status == "active"

    @property
    def waiting(self) -> bool:
        return self.status == "waiting"

    @property
    def paused(self) -> bool:
        return self.status == "paused"

    @property
    def failed(self) -> bool:
        return self.status == "error"

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    @property
    def total_length(self) -> int:
        return int(self._data["totalLength"])

    @property
    def completed_length(self) -> int:
        return float(self._data["completedLength"])

    @property
    def download_speed(self) -> int:
        return int(self._data["downloadSpeed"])

    @property
    def info_hash(self) -> Optional[str]:
        return self._data.get.get("infoHash")

    @property
    def num_seeders(self) -> int:
        return int(self._data.get["numSeeders"])

    @property
    def seeder(self) -> bool:
        res = self._data.get("seeder")
        return res if res is not None else False

    @property
    def connections(self) -> int:
        return int(self._data["connections"])

    @property
    def error_code(self) -> Optional[str]:
        return self._data.get("errorCode")

    @property
    def error_message(self) -> Optional[str]:
        return self._data.get("errorMessage")

    @property
    def dir(self) -> Path:
        return Path(self._data["dir"])

    @property
    def files(self) -> List[File]:
        if not self._files:
            self._files = [File(data) for data in self._data.get("files", [])]

        return self._files

    @property
    def bittorrent(self) -> Optional[BitTorrent]:
        if not self._bittorrent and "bittorrent" in self._data:
            self._bittorrent = BitTorrent(self._data.get("bittorrent"))
        return self._bittorrent

    @property
    def metadata(self) -> bool:
        return bool(self.followed_by)

    @property
    def followed_by(self) -> List[str]:
        return self._data.get("followedBy", [])

    @property
    def progress(self) -> float:
        try:
            return self.completed_length / self.total_length
        except ZeroDivisionError:
            return 0.0

    @property
    def eta(self) -> Union[int, str]:
        try:
            return round(
                (self.total_length - self.completed_length) /
                self.download_speed
            )
        except ZeroDivisionError:
            return "N/A"

    @async_property
    async def remove(self, force: bool = False) -> bool:
        if force is True:
            func = self.client.forceRemove
        else:
            func = self.client.remove

        res = await func(self.gid)
        if isinstance(res, str):
            return True

        return False

    @async_property
    async def pause(self, force: bool = False) -> bool:
        if force is True:
            func = self.client.forcePause
        else:
            func = self.client.pause

        res = await func(self.gid)
        if isinstance(res, str):
            return True

        return False

    @async_property
    async def resume(self) -> bool:
        res = await self.client.unpause(self.gid)
        if isinstance(res, str):
            return True

        return False
