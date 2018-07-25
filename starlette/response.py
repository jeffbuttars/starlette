from aiofiles.os import stat as aio_stat
from email.utils import formatdate
from mimetypes import guess_type
from starlette.datastructures import MutableHeaders
from starlette.types import Receive, Send
from starlette.utils import encode_json
import aiofiles
import hashlib
import os
import typing


class Response:
    media_type = None
    charset = "utf-8"

    def __init__(
        self,
        content: typing.Any,
        status_code: int = 200,
        headers: dict = None,
        media_type: str = None,
    ) -> None:
        self.body = self.render(content)
        self.status_code = status_code
        if media_type is not None:
            self.media_type = media_type
        self.init_headers(headers)

    def render(self, content: typing.Any) -> bytes:
        if isinstance(content, bytes):
            return content
        return content.encode(self.charset)

    def init_headers(self, headers):
        if headers is None:
            raw_headers = []
            populate_content_length = True
            populate_content_type = True
        else:
            raw_headers = [
                (k.lower().encode("latin-1"), v.encode("latin-1"))
                for k, v in headers.items()
            ]
            keys = [h[0] for h in raw_headers]
            populate_content_length = b"content-length" in keys
            populate_content_type = b"content-type" in keys

        body = getattr(self, "body", None)
        if body is not None and populate_content_length:
            content_length = str(len(body))
            raw_headers.append((b"content-length", content_length.encode("latin-1")))

        content_type = self.media_type
        if content_type is not None and populate_content_type:
            if content_type.startswith("text/"):
                content_type += "; charset=" + self.charset
            raw_headers.append((b"content-type", content_type.encode("latin-1")))

        self.raw_headers = raw_headers

    @property
    def headers(self):
        if not hasattr(self, "_headers"):
            self._headers = MutableHeaders(self.raw_headers)
        return self._headers

    async def __call__(self, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )
        await send({"type": "http.response.body", "body": self.body})


#  class WebSocketResponse(Response):
#      """
#      WebSocketResponse is used only when a WebSocket handler has finished.
#      Usually, there is no content and the socket may need to be closed by the app
#      if handlers are not required to close the socket.

#      WebSocket responses are always text, bytes or nothing.

#      NOTE: Only supporting None for now until the behavior is formalized.
#      Need to decide if a final payload returned from a handler is supported or not.
#      """
#      def render(self, content: None) -> None:
#          if content is None:
#              return content

#          raise RuntimeError(
#              "%s content must be None. Got %s." %
#              (self.__class__.__name__, type(content).__name__)
#          )

    # If final payloads are supported
    #  def render(self, content: typing.Any) -> typing.Union[str, bytes, None]:
    #      if content is None or isinstance(content, (bytes, str)):
    #          return content

    #      raise RuntimeError(
    #          "%s content must be string or bytes. Got %s." %
    #          (self.__class__.__name__, type(content).__name__)
    #      )


class HTMLResponse(Response):
    media_type = "text/html"


class PlainTextResponse(Response):
    media_type = "text/plain"


class JSONResponse(Response):
    media_type = "application/json"

    def render(self, content: typing.Any) -> bytes:
        return encode_json(content).encode("utf-8")


class StreamingResponse(Response):
    def __init__(
        self,
        content: typing.Any,
        status_code: int = 200,
        headers: dict = None,
        media_type: str = None,
    ) -> None:
        self.body_iterator = content
        self.status_code = status_code
        self.media_type = self.media_type if media_type is None else media_type
        self.init_headers(headers)

    async def __call__(self, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )
        async for chunk in self.body_iterator:
            if not isinstance(chunk, bytes):
                chunk = chunk.encode(self.charset)
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})


class FileResponse(Response):
    chunk_size = 4096

    def __init__(
        self,
        path: str,
        headers: dict = None,
        media_type: str = None,
        filename: str = None,
        stat_result: os.stat_result = None,
    ) -> None:
        self.path = path
        self.status_code = 200
        self.filename = filename
        if media_type is None:
            media_type = guess_type(filename or path)[0] or "text/plain"
        self.media_type = media_type
        self.init_headers(headers)
        if self.filename is not None:
            content_disposition = 'attachment; filename="{}"'.format(self.filename)
            self.headers.setdefault("content-disposition", content_disposition)
        self.stat_result = stat_result
        if stat_result is not None:
            self.set_stat_headers(stat_result)

    def set_stat_headers(self, stat_result):
        content_length = str(stat_result.st_size)
        last_modified = formatdate(stat_result.st_mtime, usegmt=True)
        etag_base = str(stat_result.st_mtime) + "-" + str(stat_result.st_size)
        etag = hashlib.md5(etag_base.encode()).hexdigest()
        self.headers.setdefault("content-length", content_length)
        self.headers.setdefault("last-modified", last_modified)
        self.headers.setdefault("etag", etag)

    async def __call__(self, receive: Receive, send: Send) -> None:
        if self.stat_result is None:
            stat_result = await aio_stat(self.path)
            self.set_stat_headers(stat_result)
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )
        async with aiofiles.open(self.path, mode="rb") as file:
            more_body = True
            while more_body:
                chunk = await file.read(self.chunk_size)
                more_body = len(chunk) == self.chunk_size
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": False}
                )
