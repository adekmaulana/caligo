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
        self.start = False

        self.log = self.api.log

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

        trigger = [
            (self.on_download_start, "Start"),
            (self.on_download_complete, "Complete"),
            (self.on_download_error, "Error")
        ]
        for handler, name in trigger:
            client.register(handler, f"aria2.onDownload{name}")
        return client

    async def get_download(
        self,
        client: aioaria2.Aria2WebsocketTrigger,
        gid: str
    ) -> util.aria2.Download:
        res = await client.tellStatus(gid)
        return await util.run_sync(util.aria2.Download, client, res)

    async def on_download_start(
        self,
        trigger: aioaria2.Aria2WebsocketTrigger,
        data: Union[Dict[str, str], Any]
    ):
        gid = data["params"][0]["gid"]
        self.api.downloads[gid] = await self.get_download(trigger, gid)
        self.log.info(f"Starting download: [gid: '{gid}']")

        # Only create task once, because we running on forever loop
        if self.start is False:
            self.start = True
            self.api.bot.loop.create_task(await self.api.updateProgress())

    async def on_download_complete(
        self,
        trigger: aioaria2.Aria2WebsocketTrigger,
        data: Union[Dict[str, str], Any]
    ):
        gid = data["params"][0]["gid"]

        self.api.downloads[gid] = await self.api.downloads[gid].update
        file = self.api.downloads[gid]
        queue = self.api.data[gid]

        meta = ""
        if file.metadata is True:
            newGid = file.followed_by[0]
            queue.put_nowait(newGid)
            meta += " - Metadata"
        else:
            await self.api.invoker.reply("Completed download: `{file.name}`")

        self.log.info(f"Complete download: [gid: '{gid}']{meta}")
        del self.api.downloads[gid]

    async def on_download_error(
        self,
        trigger: aioaria2.Aria2WebsocketTrigger,
        data: Union[Dict[str, str], Any]
    ):
        gid = data["params"][0]["gid"]

        file = await self.get_download(trigger, gid)
        self.log.warning(file.error_message)


class Aria2(module.Module):
    name: ClassVar[str] = "Aria2"

    client: Aria2WebSocket
    data: Dict[str, asyncio.Queue]
    downloads: Dict[str, str]

    invoker: pyrogram.types.Message
    progress_string: Dict[str, str]

    cancelled: bool
    stopping: bool

    async def on_load(self) -> None:
        self.data = {}
        self.downloads = {}
        self.client = await Aria2WebSocket.init(self)

        self.invoker = None
        self.progress_string = ""

        self.cancelled = False
        self.stopping = False

    async def on_stop(self) -> None:
        self.stopping = True
        await self.client.close()

    async def addDownload(self, uri: str, msg: pyrogram.types.Message) -> str:
        gid = await self.client.addUri([uri])

        # Save the message but delete first so we don't spam chat with new download
        if self.invoker is not None:
            await self.invoker.delete()
        self.invoker = msg

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

    async def checkProgress(self) -> None:
        progress_string = ""
        time = util.time.format_duration_us
        human = util.aria2.human_readable_bytes
        for file in self.downloads.values():
            file = await file.update
            downloaded = file.completed_length
            file_size = file.total_length
            percent = file.progress
            speed = file.download_speed
            eta = time(file.eta) if file.eta != "N/A" else file.eta

            bullets = "●" * int(round(percent * 10)) + "○"
            if len(bullets) > 10:
                bullets = bullets.replace("○", "")

            space = '   ' * (10 - len(bullets))
            if downloaded == file_size or file.complete:
                continue

            progress_string += (
                f"`{file.name}`\nGID: `{file.gid}`\n"
                f"Status: **{file.status}**\n"
                f"Progress: [{bullets + space}] {round(percent * 100)}%\n"
                f"{human(downloaded)} of {human(file_size)} @ "
                f"{human(speed, postfix='/s')}\neta - {eta}\n\n"
            )

        self.progress_string = progress_string

    async def updateProgress(self) -> None:
        while not self.stopping:
            if self.downloads:
                await self.checkProgress()
                progress = self.progress_string
                await self.invoker.edit(progress)
                await asyncio.sleep(5)
            else:
                await asyncio.sleep(1)

    async def cmd_test(self, ctx):
        await self.addDownload(ctx.input, ctx.msg)
