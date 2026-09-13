"""Generate Python gRPC stubs from proto/document/v1/document.proto.

Generated code lands in src/ocr_engine/proto_gen (git-ignored); CI and dev
run this before tests/launch - committed generated code always drifts.
"""

from __future__ import annotations

import sys
from pathlib import Path

from grpc_tools import protoc

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "ocr_engine" / "proto_gen"


def main() -> int:
    rc = protoc.main(
        [
            "protoc",
            f"-I{ROOT / 'proto'}",
            f"--python_out={OUT}",
            f"--grpc_python_out={OUT}",
            str(ROOT / "proto" / "document" / "v1" / "document.proto"),
        ]
    )
    if rc != 0:
        return rc

    grpc_module = OUT / "document" / "v1" / "document_pb2_grpc.py"
    fixed = grpc_module.read_text(encoding="utf-8").replace(
        "from document.v1 import document_pb2 as document_dot_v1_dot_document__pb2",
        "from ocr_engine.proto_gen.document.v1 import document_pb2 as document_dot_v1_dot_document__pb2",
    )
    grpc_module.write_text(fixed, encoding="utf-8")
    (OUT / "document" / "__init__.py").touch(exist_ok=True)
    (OUT / "document" / "v1" / "__init__.py").touch(exist_ok=True)
    print(f"generated stubs in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
