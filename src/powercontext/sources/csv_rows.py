import csv
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import quote

from powercontext.core import SourceDefinition, SourceInput, SourceOutput
from powercontext.sources import register_source


@dataclass(frozen=True)
class CsvRowInput(SourceInput):
    document: bytes
    document_id: str
    primary_key: str


@dataclass(frozen=True)
class CsvRowOutput(SourceOutput):
    document_id: str
    row_key: str
    row: tuple[tuple[str, str], ...]


class CsvRowSource(SourceDefinition[CsvRowInput, CsvRowOutput]):
    name = "csv-row"

    def parse(self, inp: CsvRowInput) -> Iterable[CsvRowOutput]:
        doc_bytes = inp.document
        # 处理UTF-8 BOM
        if doc_bytes.startswith(b"\xef\xbb\xbf"):
            text = doc_bytes[3:].decode("utf-8")
        else:
            text = doc_bytes.decode("utf-8")

        lines = text.splitlines()
        reader = csv.DictReader(lines)
        headers = tuple(reader.fieldnames) if reader.fieldnames else ()

        seen_keys = set()

        for row_dict in reader:
            pk_val = row_dict.get(inp.primary_key)
            if pk_val is None or str(pk_val).strip() == "":
                raise ValueError("primary key value cannot be empty")

            if pk_val in seen_keys:
                raise ValueError(f"duplicate primary key: {pk_val}")
            seen_keys.add(pk_val)

            # 按表头顺序组装元组
            row_tuple = tuple((h, row_dict[h]) for h in headers)
            row_key = quote(pk_val, safe="")

            yield CsvRowOutput(
                document_id=inp.document_id,
                row_key=row_key,
                row=row_tuple
            )


register_source(CsvRowSource())
