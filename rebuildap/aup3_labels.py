"""Read label tracks at full precision straight from an ``.aup3``.

``GetInfo: Type=Labels`` rounds label times to six significant digits -- 1 ms at
100 s, ~22 samples at 44.1 kHz. That is enough to let ``quantize`` write rounded
beat times back and call an off-grid track "already quantized". The ``.aup3``
itself stores the exact doubles.

An ``.aup3`` is SQLite. Tables ``project`` and ``autosave`` each hold one row
``(id=1, dict, doc)``: ``autosave`` is the current state including unsaved edits
(Audacity writes it whenever it records an undo step, and empties it on Save),
``project`` the last Save. So ``autosave`` wins when it has a row.

``dict`` and ``doc`` are Audacity's binary XML (``ProjectSerializer``),
little-endian: one field-type byte, then a payload per type. ``dict`` holds the
name table that ``doc``'s ids refer to, and the character size it sets carries
over to ``doc``. The layout is undocumented and was worked out against Audacity
3.7.9 on 2026-09-30, so anything unexpected -- an unknown field type, a blob that
does not end exactly where its last field does -- raises :class:`Aup3ReadError`
rather than yielding plausible-looking times. Callers fall back to ``GetInfo``.

Opened read-only (``mode=ro``): the file is the user's working copy, and may be
open in Audacity. Measured safe on a live project: the ``.aup3``, ``-wal`` and
``-shm`` stayed byte-identical and Audacity went on editing and saving.
"""

import sqlite3
import struct
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

Label = Tuple[float, float, str]
LabelTrack = Tuple[str, List[Label]]

# Tables in the order they are consulted: unsaved state first, then the last Save.
STATE_TABLES = ("autosave", "project")
STATE_ROW_ID = 1

# ProjectSerializer field types.
FT_CHAR_SIZE = 0
FT_START_TAG = 1
FT_END_TAG = 2
FT_STRING = 3
FT_INT = 4
FT_BOOL = 5
FT_LONG = 6
FT_LONG_LONG = 7
FT_SIZE_T = 8
FT_FLOAT = 9
FT_DOUBLE = 10
FT_DATA = 11
FT_RAW = 12
FT_PUSH = 13
FT_POP = 14
FT_NAME = 15

# Before any CharSize field is seen.
DEFAULT_CHAR_SIZE = 1
ENCODING_BY_CHAR_SIZE = {1: "utf-8", 2: "utf-16-le", 4: "utf-32-le"}

TAG_LABEL_TRACK = "labeltrack"
TAG_LABEL = "label"
ATTR_NAME = "name"
ATTR_START = "t"
ATTR_END = "t1"
ATTR_TITLE = "title"

EVENT_START = "start"
EVENT_END = "end"
EVENT_ATTR = "attr"


class Aup3ReadError(Exception):
    """The ``.aup3`` could not be read, or held something the parser does not know."""


class _Reader:
    """Walks one binary-XML blob, reporting tags and attributes to ``on``."""

    def __init__(self, blob: bytes, names: Dict[int, str], char_size: List[int]):
        self.blob = blob
        self.pos = 0
        self.names = names
        # Shared, one-element list: the size set while reading ``dict`` applies to ``doc``.
        self.char_size = char_size

    def _unpack(self, fmt: str):
        (value,) = struct.unpack_from(fmt, self.blob, self.pos)
        self.pos += struct.calcsize(fmt)
        return value

    def _name(self) -> str:
        name_id = self._unpack("<H")
        try:
            return self.names[name_id]
        except KeyError:
            raise Aup3ReadError(f"name id {name_id} not in the name table") from None

    def _string(self, length: int) -> str:
        raw = self.blob[self.pos : self.pos + length]
        if len(raw) != length:
            raise Aup3ReadError("string runs past the end of the blob")
        self.pos += length
        encoding = ENCODING_BY_CHAR_SIZE.get(self.char_size[0])
        if encoding is None:
            raise Aup3ReadError(f"unknown character size {self.char_size[0]}")
        return raw.decode(encoding)

    def walk(self, on: Callable[[str, str, object], None]) -> None:
        # Payloads that are one fixed-size number after the attribute's name id.
        numbers = {
            FT_INT: "<i",
            FT_BOOL: "<B",
            FT_LONG: "<i",
            FT_LONG_LONG: "<q",
            FT_SIZE_T: "<I",
        }
        try:
            while self.pos < len(self.blob):
                field_type = self._unpack("<B")
                if field_type == FT_CHAR_SIZE:
                    self.char_size[0] = self._unpack("<B")
                elif field_type == FT_START_TAG:
                    on(EVENT_START, self._name(), None)
                elif field_type == FT_END_TAG:
                    on(EVENT_END, self._name(), None)
                elif field_type == FT_STRING:
                    key = self._name()
                    on(EVENT_ATTR, key, self._string(self._unpack("<i")))
                elif field_type in numbers:
                    key = self._name()
                    on(EVENT_ATTR, key, self._unpack(numbers[field_type]))
                elif field_type in (FT_FLOAT, FT_DOUBLE):
                    key = self._name()
                    value = self._unpack("<f" if field_type == FT_FLOAT else "<d")
                    self._unpack("<i")  # digits: display precision, not needed
                    on(EVENT_ATTR, key, value)
                elif field_type in (FT_DATA, FT_RAW):
                    self._string(self._unpack("<i"))
                elif field_type in (FT_PUSH, FT_POP):
                    pass
                elif field_type == FT_NAME:
                    name_id = self._unpack("<H")
                    self.names[name_id] = self._string(self._unpack("<H"))
                else:
                    raise Aup3ReadError(
                        f"unknown field type {field_type} at byte {self.pos - 1}"
                    )
        except struct.error as e:
            raise Aup3ReadError(f"blob ends mid-field: {e}") from e
        except UnicodeDecodeError as e:
            raise Aup3ReadError(f"undecodable string: {e}") from e


def parse_label_tracks(dict_blob: bytes, doc_blob: bytes) -> List[LabelTrack]:
    """Label tracks, in track order, from one ``(dict, doc)`` row."""
    names: Dict[int, str] = {}
    char_size = [DEFAULT_CHAR_SIZE]
    _Reader(dict_blob, names, char_size).walk(lambda *event: None)

    tracks: List[Tuple[Dict[str, object], List[Dict[str, object]]]] = []
    current: List[Optional[Tuple[Dict[str, object], List[Dict[str, object]]]]] = [None]

    def on(event: str, key: str, value: object) -> None:
        if event == EVENT_START and key == TAG_LABEL_TRACK:
            current[0] = ({}, [])
            tracks.append(current[0])
        elif current[0] is None:
            return
        elif event == EVENT_START and key == TAG_LABEL:
            current[0][1].append({})
        elif event == EVENT_END and key == TAG_LABEL_TRACK:
            current[0] = None
        elif event == EVENT_ATTR:
            track_attrs, labels = current[0]
            (labels[-1] if labels else track_attrs)[key] = value

    _Reader(doc_blob, names, char_size).walk(on)

    try:
        return [
            (
                str(attrs[ATTR_NAME]),
                [
                    (
                        float(label[ATTR_START]),
                        float(label[ATTR_END]),
                        str(label.get(ATTR_TITLE, "")),
                    )
                    for label in labels
                ],
            )
            for attrs, labels in tracks
        ]
    except KeyError as e:
        raise Aup3ReadError(f"label track or label without {e}") from e


def read_label_tracks(aup3: Path) -> List[LabelTrack]:
    """Every label track in ``aup3`` as ``(name, [(start, end, text), ...])``,
    from the unsaved state when there is one, else from the last Save.

    Raises :class:`Aup3ReadError` for anything short of a clean read.
    """
    if not aup3.is_file():
        raise Aup3ReadError(f"{aup3} does not exist")
    try:
        con = sqlite3.connect(f"{aup3.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise Aup3ReadError(f"cannot open {aup3}: {e}") from e
    try:
        for table in STATE_TABLES:
            row = con.execute(
                f"SELECT dict, doc FROM {table} WHERE id=?", (STATE_ROW_ID,)
            ).fetchone()
            if row is not None:
                return parse_label_tracks(bytes(row[0]), bytes(row[1]))
    except sqlite3.Error as e:
        raise Aup3ReadError(f"cannot read {aup3}: {e}") from e
    finally:
        con.close()
    raise Aup3ReadError(f"{aup3} holds no project state")
