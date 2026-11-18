"""Row-level CSV Source: one CAPTURED Source per record, addressable by document + primary key."""
from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    Source,
    SourceDefinition,
    SourceMaterialization,
    TextEvidence,
)

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_DEFINITION_VERSION = "1"


class CSVDocumentInput(BaseModel):
    """One offline CSV document to be split into row Sources."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One fully validated record, preserving header order and raw values."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """Captured row evidence whose identity is document + primary key value."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the bounded, delimiter-safe identity of one row."""
    _validate_document_id(document_id)
    if not primary_key_value.strip():
        raise ValueError("primary key value cannot be empty")
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("csv row source identity exceeds the maximum length")
    return identity


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Validate the whole document atomically, then return every accepted row."""
    _validate_document_id(value.document_id)
    if not value.primary_key.strip():
        raise ValueError("primary key column name cannot be empty")

    text = _decode_document(value.content)
    records = _read_records(text)
    if not records:
        raise ValueError("csv document has no header record")

    header = records[0]
    if not header:
        raise ValueError("csv header record is empty")
    for column in header:
        if not column.strip():
            raise ValueError("csv header field cannot be empty")
    if len(set(header)) != len(header):
        raise ValueError("csv header fields must be unique")
    try:
        key_index = header.index(value.primary_key)          # 精确匹配，不 trim
    except ValueError:
        raise ValueError("primary key column is not present in the header") from None

    width = len(header)
    seen: set[str] = set()
    rows: list[CSVRowInput] = []
    for record in records[1:]:
        if not record:                                        # 完全空物理记录忽略
            continue
        if len(record) != width:
            raise ValueError("csv record width does not match the header")
        key = record[key_index]
        if not key.strip():
            raise ValueError("primary key value cannot be empty")
        if key in seen:
            raise ValueError("duplicate primary key value")
        seen.add(key)
        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=key,
                columns=tuple(header),
                values=tuple(record),
            )
        )
    return tuple(rows)


def _decode_document(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")                    # 严格 UTF-8 + 仅开头 BOM
    except UnicodeDecodeError as error:
        raise ValueError("csv document is not valid UTF-8") from error


def _read_records(text: str) -> list[list[str]]:
    try:
        # newline="" 让 csv 自己处理 CRLF 与单元格内换行；strict=True 拒绝畸形引号
        return list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:                                # csv.Error 不是 ValueError
        raise ValueError("csv document is malformed") from error


def _validate_document_id(document_id: str) -> None:
    if not document_id or not document_id.strip():
        raise ValueError("document id cannot be empty")
    if document_id != document_id.strip():
        raise ValueError("document id must not contain leading or trailing whitespace")


class _CSVRowTextEvidenceProjection:
    """Standard text-evidence projection: compact JSON of the record."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


class _CSVRowSourceDefinition:
    """Bind the row adapter to version 1 and the standard text-evidence projection."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    version = CSV_ROW_DEFINITION_VERSION
    source_class = CSVRowSource
    projections = (_CSVRowTextEvidenceProjection(),)

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=identity,                                     # name 与 source_id 都是 identity
            source_id=identity,
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,    # 必须是 CAPTURED
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = (
    _CSVRowSourceDefinition()
)
