# -*- coding: utf-8 -*-
"""老版 Word (.doc) 解析器 —— 先经 LibreOffice 转成 .docx 再解析。

框架 KB 的 WordParser 基于 python-docx，只能读 OOXML 格式的 .docx；
老式 .doc 是 OLE2 二进制复合文档，python-docx 无法打开，上传后索引
worker 会报 "No parser registered for media type 'application/msword'"
并将文档置为 error 状态。

本解析器用 LibreOffice（soffice --headless）把 .doc 转换为 .docx，
再复用 WordParser 完成解析。注册 media type: application/msword。
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from agentscope.rag import Section, WordParser
from agentscope.rag._parser._base import ParserBase


def _find_soffice() -> str | None:
    """定位 soffice 可执行文件（PATH 优先，兜底常见安装路径）。"""
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for cand in (
        "/home/agentrunner/.local/bin/soffice",  # 当前部署实际路径
        "/usr/bin/soffice",
        "/opt/libreoffice/program/soffice",
        # Windows 常见安装路径（本地开发机）
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if Path(cand).exists():
            return cand
    return None


class DocParser(ParserBase):
    """老版 Word ``.doc`` 解析器（application/msword）。

    转换在每个文档独立的临时目录中进行，并为 soffice 指定独立的
    UserInstallation profile —— LibreOffice 默认 profile 有单实例
    锁，多文档并发转换若共享 profile 会互相卡死。
    """

    supported_media_types = ["application/msword"]

    def __init__(self, timeout: float = 120.0) -> None:
        """初始化。

        Args:
            timeout (`float`, defaults to ``120.0``):
                单个文档 LibreOffice 转换超时（秒），超时按解析失败
                处理（文档置为 error 状态）。
        """
        self._word = WordParser(include_image=False)
        self._timeout = timeout

    @classmethod
    def supported_extensions(cls) -> list[str]:
        """返回 ``[".doc"]``（前端上传选择器的 accept 列表）。"""
        return [".doc"]

    async def parse(
        self,
        file: bytes | str,
        filename: str,
    ) -> list[Section]:
        """把 .doc 转为 .docx 后交给 WordParser 解析。

        Args:
            file (`bytes | str`):
                原始 .doc 字节内容，或文件系统路径。
            filename (`str`):
                原始文件名，进入 Section.source 用于引用展示。

        Returns:
            `list[Section]`: 按文档顺序的解析结果。

        Raises:
            `ValueError`: soffice 不可用、转换失败或超时。
        """
        soffice = _find_soffice()
        if soffice is None:
            raise ValueError(
                "LibreOffice (soffice) is required to parse legacy .doc "
                "files but was not found on this system.",
            )

        with tempfile.TemporaryDirectory(prefix="kb_doc_") as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "source.doc"
            if isinstance(file, bytes):
                src.write_bytes(file)
            else:
                shutil.copyfile(file, src)

            profile = tmp_path / "lo_profile"
            proc = await asyncio.create_subprocess_exec(
                soffice,
                f"-env:UserInstallation=file://{profile}",
                "--headless",
                "--norestore",
                "--convert-to", "docx",
                "--outdir", str(tmp_path),
                str(src),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=self._timeout)
            except asyncio.TimeoutError:
                proc.kill()
                raise ValueError(
                    f"LibreOffice conversion timed out for {filename!r} "
                    f"after {self._timeout}s.",
                ) from None

            converted = tmp_path / "source.docx"
            if proc.returncode != 0 or not converted.exists():
                raise ValueError(
                    f"Failed to convert {filename!r} to .docx "
                    f"(exit code {proc.returncode}).",
                )
            return await self._word.parse(str(converted), filename)
