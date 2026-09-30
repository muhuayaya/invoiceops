"""UTF-8 CSV upload validation shared by the API and worker."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_EXTERNAL_ID_LENGTH = 255


class InvalidBatch(ValueError):
    pass


@dataclass(frozen=True)
class BatchRow:
    row_number: int
    text: str
    external_id: str | None
    error_code: str | None = None
    error_reason: str | None = None


def parse_batch(
    content: bytes,
    *,
    max_bytes: int = MAX_FILE_BYTES,
    max_rows: int = 10_000,
    max_text_length: int = 10_000,
) -> list[BatchRow]:
    if len(content) > max_bytes:
        raise InvalidBatch("CSV exceeds maximum file size")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidBatch("CSV must be UTF-8") from exc
    try:
        reader = csv.DictReader(io.StringIO(decoded, newline=""), strict=True)
        if reader.fieldnames is None or "text" not in reader.fieldnames:
            raise InvalidBatch("CSV requires a text header")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise InvalidBatch("CSV has duplicate headers")
        unsupported_headers = set(reader.fieldnames) - {"text", "external_id"}
        if unsupported_headers:
            raise InvalidBatch("CSV has unsupported headers")
        rows: list[BatchRow] = []
        for record in reader:
            if len(rows) >= max_rows:
                raise InvalidBatch("CSV exceeds maximum row count")
            row_number = reader.line_num
            external_id = record.get("external_id")
            text = record.get("text")
            if None in record:
                rows.append(BatchRow(row_number, "", external_id, "invalid_columns", "row has more columns than the header"))
            elif external_id is not None and len(external_id) > MAX_EXTERNAL_ID_LENGTH:
                rows.append(BatchRow(row_number, "", external_id, "external_id_too_long", "external_id exceeds maximum length"))
            elif text is None or not text.strip():
                rows.append(BatchRow(row_number, "", external_id, "empty_text", "text must not be empty"))
            elif len(text) > max_text_length:
                rows.append(BatchRow(row_number, "", external_id, "text_too_long", "text exceeds maximum length"))
            else:
                rows.append(BatchRow(row_number, text, external_id))
        return rows
    except csv.Error as exc:
        raise InvalidBatch("CSV cannot be parsed") from exc
