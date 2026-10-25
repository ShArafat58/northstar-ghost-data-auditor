"""Read-only, label-free triage of missing evidence and false-completion risk.

This tool is diagnostic only. A raw identity mention is a retrieval candidate,
NOT proof of a resource's present/absent state or a deletion obligation.
No sample reference output is loaded and no predictions are modified.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

CODE = Path(__file__).resolve().parents[1]
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))

from baseline import _case_observations
from identity import IdentityResolver, ResolvedIdentity
from intent_v2 import authorizes_deletion_v2
from loaders import DeletionRequest, EvidenceFile, load_dataset
from observations import EvidenceIndex, RESOURCE, build_index


def aliases_for(identity: ResolvedIdentity) -> dict[str, frozenset[str]]:
    return {
        "user_id": frozenset((identity.user_id.casefold(),)),
        "email": frozenset(x.casefold() for x in identity.emails if x),
        "phone": frozenset(x.casefold() for x in identity.phones if x),
        "email_hash": frozenset(x.casefold() for x in identity.email_hashes if x),
    }


def exact_token_in_text(content: str, token: str) -> bool:
    """Prevent a `user_7` hit for `user_70` and an e-mail suffix hit."""
    if not token or len(token) < 4:
        return False
    return re.search(r"(?<![\w@.+-])" + re.escape(token) + r"(?![\w@.+-])", content, re.I) is not None


def find_alias_types(content: str, aliases: dict[str, frozenset[str]]) -> list[str]:
    return [kind for kind, tokens in aliases.items()
            if any(exact_token_in_text(content, token) for token in tokens)]


def read_evidence_text(file: EvidenceFile, *, use_ocr: bool) -> tuple[str | None, str | None]:
    suffix = file.path.suffix.lower()
    try:
        if suffix in {".json", ".sql", ".txt", ".log"}:
            return file.path.read_text(encoding="utf-8-sig", errors="replace"), None
        if suffix == ".pdf":
            import pymupdf
            with pymupdf.open(file.path) as doc:
                return "\n".join(page.get_text() for page in doc), None
        if suffix == ".png":
            if not use_ocr:
                return None, "OCR_SKIPPED"
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(file.path)), None
        return None, "UNSUPPORTED_EXTENSION"
    except (ImportError, FileNotFoundError, OSError, RuntimeError) as exc:
        return None, type(exc).__name__
    except Exception as exc:
        # OCR executables can raise pytesseract-specific error subclasses.
        return None, type(exc).__name__


def find_raw_candidates(request: DeletionRequest, identity: ResolvedIdentity,
                        files: Iterable[EvidenceFile], *, use_ocr: bool = True,
                        cache: dict[str, tuple[str | None, str | None]] | None = None) -> list[dict[str, Any]]:
    aliases = aliases_for(identity)
    results: list[dict[str, Any]] = []
    for file in files:
        if file.captured_at > request.audit_at:
            continue
        key = str(file.path)
        if cache is not None and key in cache:
            content, error = cache[key]
        else:
            content, error = read_evidence_text(file, use_ocr=use_ocr)
            if cache is not None:
                cache[key] = (content, error)
        if not content:
            continue
        alias_types = find_alias_types(content, aliases)
        if not alias_types:
            continue
        # Resource IDs on a line mentioning an exact alias are candidates,
        # never inferred ownership edges. Do not print raw identity values.
        resource_candidates: set[str] = set()
        matches = 0
        for line in content.splitlines():
            if find_alias_types(line, aliases):
                matches += 1
                resource_candidates.update(x for x in RESOURCE.findall(line)
                                           if re.search(r"_\d", x))
        results.append({"evidence_id": file.file_id,
                        "kind": file.path.suffix.lower().lstrip("."),
                        "alias_types": alias_types,
                        "matching_lines": matches,
                        "same_line_resource_candidates": sorted(resource_candidates)[:25],
                        "note": "Identity mention only; not state proof"})
    return results


def summarize_case(request: DeletionRequest, identity: ResolvedIdentity | None,
                   index: EvidenceIndex, files: Iterable[EvidenceFile],
                   *, cache: dict[str, tuple[str | None, str | None]], use_ocr: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"case_id": request.case_id,
                              "intent_authorized": authorizes_deletion_v2(request.request_text),
                              "identity_resolved": identity is not None,
                              "indexed_resources": [],
                              "indexed_evidence_ids": [],
                              "raw_candidate_files": [],
                              "diagnosis": ""}
    if not result["intent_authorized"]:
        result["diagnosis"] = "OUT_OF_SCOPE_INTENT"
        return result
    if identity is None:
        result["diagnosis"] = "UNRESOLVED_IDENTITY"
        return result
    grouped = _case_observations(index, identity, request.audit_at)
    result["indexed_resources"] = sorted({rid for _, rid in grouped})
    result["indexed_evidence_ids"] = sorted({o.file_id for history in grouped.values() for o in history})
    result["raw_candidate_files"] = find_raw_candidates(request, identity, files,
                                                         cache=cache, use_ocr=use_ocr)
    if not grouped:
        result["diagnosis"] = ("RAW_IDENTITY_FOUND_BUT_NOT_INDEXED"
                               if result["raw_candidate_files"] else
                               "NO_DIRECT_IDENTITY_MENTIONS_BEFORE_CUTOFF")
    elif result["raw_candidate_files"]:
        raw_ids = {x["evidence_id"] for x in result["raw_candidate_files"]}
        index_ids = set(result["indexed_evidence_ids"])
        result["diagnosis"] = ("ADDITIONAL_UNINDEXED_SOURCE_CANDIDATES"
                               if raw_ids - index_ids else "INDEXED_EVIDENCE_PRESENT")
    else:
        result["diagnosis"] = "INDEXED_RESOURCE_VIA_NON_RAW_LINK"
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case-ids", nargs="+", required=True, metavar="CASE_ID")
    p.add_argument("--output", type=Path, default=Path("code/evaluation/evidence_gap_report.json"))
    p.add_argument("--skip-ocr", action="store_true")
    args = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    dataset = load_dataset(root, sample=True)
    selected = set(args.case_ids)
    cases = [case for case in dataset.requests if case.case_id in selected]
    if len(cases) != len(selected):
        raise ValueError("One or more requested case IDs are not in the 32-case sample")
    resolver = IdentityResolver(dataset.identities, dataset.changes)
    print("INDEXING: readback evidence, without using sample labels")
    index = build_index(dataset.evidence, enhanced_readback=True)
    cache: dict[str, tuple[str | None, str | None]] = {}
    results = [summarize_case(case, resolver.resolve(case.email, case.audit_at),
                              index, dataset.evidence, cache=cache,
                              use_ocr=not args.skip_ocr)
               for case in sorted(cases, key=lambda c: c.case_id)]
    report = {"purpose": "Evidence-gap diagnosis; no classification or prediction changes",
              "do_not_use_as_proof": "Raw text or OCR mentions alone do not establish record state",
              "indexed_observations": len(index.observations),
              "unparsed_media": list(index.unparsed_media),
              "unreadable_files": list(index.unreadable_files),
              "cases": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for item in results:
        print(f"{item['case_id']}: {item['diagnosis']} | "
              f"index_resources={len(item['indexed_resources'])} "
              f"raw_files={len(item['raw_candidate_files'])} "
              f"indexed_files={len(item['indexed_evidence_ids'])}")
        for raw in item["raw_candidate_files"]:
            if raw["evidence_id"] not in item["indexed_evidence_ids"]:
                print(f"  UNINDEXED CANDIDATE: {raw['evidence_id']} ({raw['kind']}) "
                      f"aliases={','.join(raw['alias_types'])} "
                      f"same_line_resources={','.join(raw['same_line_resource_candidates'][:6]) or '-'}")
    print(f"REPORT: {args.output}")
    print("CAUTION: Raw matches are candidates, never deletion/absence proof.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
