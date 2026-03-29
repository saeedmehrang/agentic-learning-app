"""
get_course_structure — token-efficient course awareness tool for LessonAgent.

Merges outlines.yaml and concept_map.json into a cached lookup that exposes:
- Course overview: modules and lesson titles
- Lesson detail: objectives, prerequisites, concept relationships, sequencing
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path resolution: backend/tools/ → backend/ → repo root → courses/
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Module-level cache — loaded once, reused on every subsequent call
_COURSE_DATA: dict[str, Any] | None = None


def _load_course_data() -> dict[str, Any]:
    """
    Load and merge outlines.yaml + concept_map.json into a single in-memory
    structure. Also pre-computes comes_after / comes_before for all lessons
    and builds a module lookup keyed by lesson ID.

    Called at most once per process lifetime; result is cached in _COURSE_DATA.
    """
    global _COURSE_DATA

    if _COURSE_DATA is not None:
        return _COURSE_DATA

    outlines_path = _REPO_ROOT / "courses" / "linux-basics" / "outlines.yaml"
    concept_map_path = _REPO_ROOT / "courses" / "linux-basics" / "concept_map.json"

    with outlines_path.open("r", encoding="utf-8") as fh:
        raw_outlines: list[dict[str, Any]] = yaml.safe_load(fh)

    with concept_map_path.open("r", encoding="utf-8") as fh:
        raw_concept_map: dict[str, Any] = json.load(fh)

    # --- Index outlines by lesson_id ---
    outlines_by_id: dict[str, dict[str, Any]] = {
        entry["lesson_id"]: entry for entry in raw_outlines
    }

    # --- Module metadata from concept_map ---
    modules_raw: dict[str, dict[str, Any]] = raw_concept_map["course_metadata"]["modules"]

    # Build lesson → module lookup
    lesson_to_module: dict[str, int] = {}
    for mod_id_str, mod_data in modules_raw.items():
        mod_id = int(mod_id_str)
        for lesson_id in mod_data["lessons"]:
            lesson_to_module[lesson_id] = mod_id

    # Sorted lesson IDs (L01 … L29) for comes_after / comes_before
    all_lesson_ids: list[str] = sorted(
        outlines_by_id.keys(),
        key=lambda lid: int(lid[1:]),
    )

    # --- Merge into unified lesson records ---
    lessons: dict[str, dict[str, Any]] = {}
    for idx, lesson_id in enumerate(all_lesson_ids):
        outline = outlines_by_id[lesson_id]
        cm_entry: dict[str, Any] = raw_concept_map["lessons"].get(lesson_id, {})

        mod_id = lesson_to_module.get(lesson_id, 0)
        mod_info = modules_raw.get(str(mod_id), {})

        # prerequisites from outlines.yaml (list of strings like "L10")
        raw_prereqs = outline.get("prerequisites", [])
        prerequisites: list[str] = [str(p) for p in raw_prereqs]

        # assumes_concepts from concept_map (list of {concept, introduced_in})
        assumes_raw = cm_entry.get("assumes", [])
        assumes_concepts: list[dict[str, str]] = [
            {"concept": item["concept"], "from": item["introduced_in"]}
            for item in assumes_raw
        ]

        cross_lesson_flags: list[str] = []
        flag = cm_entry.get("cross_lesson_flag")
        if flag:
            cross_lesson_flags = [flag]

        lessons[lesson_id] = {
            "lesson_id": lesson_id,
            "title": outline["title"],
            "module_id": mod_id,
            "module_title": mod_info.get("title", ""),
            "learning_objectives": outline.get("learning_objectives", []),
            "key_concepts": outline.get("key_concepts", []),
            "prerequisites": prerequisites,
            "introduces_concepts": cm_entry.get("introduces", []),
            "assumes_concepts": assumes_concepts,
            "comes_after": all_lesson_ids[idx - 1] if idx > 0 else None,
            "comes_before": all_lesson_ids[idx + 1] if idx < len(all_lesson_ids) - 1 else None,
            "cross_lesson_flags": cross_lesson_flags,
        }

    # --- Module list for overview ---
    modules_list: list[dict[str, Any]] = [
        {
            "id": int(mod_id_str),
            "title": mod_data["title"],
            "lessons": mod_data["lessons"],
        }
        for mod_id_str, mod_data in sorted(modules_raw.items(), key=lambda kv: int(kv[0]))
    ]

    lesson_titles: dict[str, str] = {lid: data["title"] for lid, data in lessons.items()}

    _COURSE_DATA = {
        "course": raw_concept_map["course_title"],
        "total_lessons": raw_concept_map["course_metadata"]["total_lessons"],
        "modules_list": modules_list,
        "lesson_titles": lesson_titles,
        "lessons": lessons,
        "all_lesson_ids": all_lesson_ids,
    }

    logger.info(
        "Course data loaded and cached",
        extra={"total_lessons": _COURSE_DATA["total_lessons"]},
    )
    return _COURSE_DATA


async def get_course_structure(lesson_id: str | None = None) -> dict[str, Any]:
    """
    Return structured course information for the Linux Basics course.

    Use this tool only when:
    - A learner asks about a concept not in the current lesson (look up where it
      lives and deflect gracefully).
    - A learner seems to be missing prerequisite knowledge (inspect assumes_concepts).
    - A learner asks what the course covers or what topics are coming up.

    Args:
        lesson_id: Optional lesson identifier (e.g. "L11"). When omitted, returns
                   a course overview with all modules and lesson titles. When
                   provided, returns full detail for that lesson.

    Returns:
        dict: Either a course overview or a lesson detail object.
              On invalid lesson_id, returns an error dict with valid_ids.
    """
    data = _COURSE_DATA if _COURSE_DATA is not None else _load_course_data()

    if lesson_id is None:
        return {
            "mode": "overview",
            "course": data["course"],
            "total_lessons": data["total_lessons"],
            "modules": data["modules_list"],
            "lesson_titles": data["lesson_titles"],
        }

    lesson = data["lessons"].get(lesson_id)
    if lesson is None:
        return {
            "error": f"lesson_id '{lesson_id}' not found in course",
            "valid_ids": data["all_lesson_ids"],
        }

    return {
        "mode": "lesson",
        "lesson_id": lesson["lesson_id"],
        "title": lesson["title"],
        "module_id": lesson["module_id"],
        "module_title": lesson["module_title"],
        "learning_objectives": lesson["learning_objectives"],
        "key_concepts": lesson["key_concepts"],
        "prerequisites": lesson["prerequisites"],
        "introduces_concepts": lesson["introduces_concepts"],
        "assumes_concepts": lesson["assumes_concepts"],
        "comes_after": lesson["comes_after"],
        "comes_before": lesson["comes_before"],
        "cross_lesson_flags": lesson["cross_lesson_flags"],
    }
