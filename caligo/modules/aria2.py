import asyncio
from pathlib import Path
from typing import Any, ClassVar, Dict, Union

import aioaria2
import pyrogram

from .. import module, util


class Aria2WebSocket:

    server: aioaria2.AsyncAria2Server
    client: aioaria2.Aria2WebsocketTrigger

    def __init__(self, api: "Aria2") -> None:
        self.api = api

    @classmethod
    async def init(cls, api: "Aria2") -> "Aria2WebSocket":
        path = Path.home() / "downloads"
        path.mkdir(parents=True, exist_ok=True)

        link = "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt"
        async with api.bot.http.get(link) as resp:
            trackers_list: str = await resp.text()
            trackers: str = "[" + trackers_list.replace("\n\n", ",") + "]"

        cmd = [
            "aria2c",
            f"--dir={str(path)}",
            "--enable-rpc",
            "--rpc-listen-all=false",
            "--rpc-listen-port=8080",
            "--max-connection-per-server=10",
            "--rpc-max-request-size=1024M",
            "--seed-ratio=1",
            "--seed-time=60",
            "--max-upload-limit=1024K",
            "--max-concurrent-downloads=5",
            "--min-split-size=10M",
            "--follow-torrent=mem",
            "--split=10",
            f"--bt-tracker={trackers}",
            "--daemon=true",
            "--allow-overwrite=true",
        ]
        protocol = "http://localhost:8080/jsonrpc"

        cpath = Path.home() / ".cache" / "caligo" / ".certs"
        if (Path(cpath / "cert.pem").is_file() and
                Path(cpath / "key.pem").is_file()):
            cmd.insert(3, "--rpc-secure=true")
            cmd.insert(3, f"--rpc-private-key={str(cpath / 'key.pem')}")
            cmd.insert(3, f"--rpc-certificate={str(cpath / 'cert.pem')}")
            protocol = "https://localhost:8080/jsonrpc"

        server = aioaria2.AsyncAria2Server(*cmd, daemon=True)

        await server.start()
        await server.wait()

        self = cls(api)
        client = await aioaria2.Aria2WebsocketTrigger.new(url=protocol)

        trigger_names = ["Start", "Complete", "Error"]
        for handler_name in trigger_names:
            client.register(self.on_trigger, f"aria2.onDownload{handler_name}")
        return client

    async def on_trigger(
        self,
        trigger: aioaria2.Aria2WebsocketTrigger,
        data: Union[Dict[str, str], Any]
    ):
        method = data.get("method").removeprefix("aria2.")
        gid = data["params"][0]["gid"]

        if method == "onDownloadComplete":
            self.api.downloads[gid] = await self.api.downloads[gid].update
            file = self.api.downloads[gid]
            queue = self.api.data[gid]

            if file.metadata is True:
                newGid = file.followed_by[0]
                self.api.downloads[newGid] = await self.api.getDownload(newGid)
                queue.put_nowait(newGid)

        update = getattr(self.api, method)
        await update(gid)


class Aria2(module.Module):
    name: ClassVar[str] = "Aria2"

    client: Aria2WebSocket
    data: Dict[str, asyncio.Queue]
    downloads: Dict[str, str]

    invoker: pyrogram.types.Message
    progress_string: Dict[str, str]

    async def on_load(self) -> None:
        self.data = {}
        self.downloads = {}
        self.client = await Aria2WebSocket.init(self)

        self.invoker = None
        self.progress_string = {}
        self.cancelled = False

    async def on_stop(self) -> None:
        await self.client.close()

    async def onDownloadStart(self, gid: str) -> None:
        self.log.info(f"Starting download: [gid: '{gid}']")

    async def onDownloadComplete(self, gid: str):
        meta = ""
        file = self.downloads[gid]
        if file.metadata is True:
            meta += " - Metadata"

        self.log.info(f"Complete download: [gid: '{gid}']{meta}")

    async def onDownloadError(self, gid: str) -> None:
        file = await self.getDownload(gid)
        self.log.warning(file.error_message)

    async def addDownload(self, uri: str, msg: pyrogram.types.Message) -> str:
        gid = await self.client.addUri([uri])
        self.downloads[gid] = await self.getDownload(gid)

        # Save the message but delete first so we don't spam chat with new download
        if self.invoker is not None:
            await self.invoker.delete()

        self.invoker = msg

        self.bot.loop.create_task(self.checkProgress(gid))

        self.data[gid] = asyncio.Queue(1)

        try:
            fut = await asyncio.wait_for(self.data[gid].get(), 10)
        except asyncio.TimeoutError:
            fut = None
        finally:
            del self.data[gid]

        if fut is not None:
            return fut

        return gid

    async def pauseDownload(self, gid: str) -> str:
        return await self.client.pause(gid)

    async def removeDownload(self, gid: str) -> str:
        return await self.client.remove(gid)

    async def getDownload(self, gid: str) -> util.aria2.Download:
        res = await self.client.tellStatus(gid)
        return util.aria2.Download(self, res)

    async def checkProgress(self, gid: str) -> Union[str, pyrogram.types.Message]:
        complete = False
        while not complete:
            if self.cancelled is True:
                return "Cancelled"

            file = await self.downloads[gid].update
            complete = file.complete
            try:
                if not complete and not file.error_message:
                    downloaded = file.completed_length
                    file_size = file.total_length
                    percent = file.progress
                    speed = file.download_speed
                    eta = file.eta
                    bullets = "●" * int(round(percent * 10)) + "○"
                    if len(bullets) > 10:
                        bullets = bullets.replace("○", "")
                    space = '  ' * (10 - len(bullets))
                    progress_string = (
                        f"Progress: [{bullets + space}] {round(percent * 100)}%\n"
                        f"{downloaded} of {file_size} @ {speed}\n"
                        f"eta - {eta}"
                    )
                    if (self.progress_string.get(gid) is not None and
                            self.progress_string.get(gid) != progress_string) or (
                            self.progress_string.get(gid) is None):
                        await self.invoker.edit(progress_string)

                    self.progress_string[gid] = progress_string
                await asyncio.sleep(3)
                file = await self.downloads[gid].update
                complete = file.complete
                if complete:
                    del self.downloads[gid]
                    return "Completed..."
                else:
                    continue
            except Exception as e:
                self.log.info(f"ERROR: {e}")
                return str(e)

    async def cmd_test(self, ctx):
        gid = await self.addDownload(ctx.input, ctx.msg)

        ret = await self.checkProgress(gid)
        return ret
