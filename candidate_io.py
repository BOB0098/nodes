#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Candidate queue loading with fail-closed schema validation."""
import json


class CandidateQueueError(ValueError):
    """The persisted candidate queue cannot be trusted or safely consumed."""


def load_candidates(path):
    """Return candidate records; a missing queue is the supported first-run state."""
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise CandidateQueueError(
            f"候选队列读取失败 ({path}): {type(exc).__name__}") from exc

    if not isinstance(doc, dict):
        raise CandidateQueueError("候选队列 schema 错误: 顶层必须是对象")
    candidates = doc.get("candidates")
    if not isinstance(candidates, list):
        raise CandidateQueueError("候选队列 schema 错误: candidates 必须是列表")
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            raise CandidateQueueError(
                f"候选队列 schema 错误: candidates[{index}] 必须是对象")
        repo = candidate.get("repo")
        if not isinstance(repo, str) or not repo.strip():
            raise CandidateQueueError(
                f"候选队列 schema 错误: candidates[{index}].repo 必须是非空字符串")
    return candidates
