"""Evidence-bounded recipe collection derived from completed daily reports."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

from dashboard_model import frontmatter_value, markdown_section
from daily_deposition_receipt import source_deposition_state

RECIPE_HEADING = "Recipe Candidates"
AMOUNT_UNITS = ("kg", "g", "mL", "ml", "L", "l", "包", "袋", "罐", "盒", "瓶", "个", "只", "片", "勺", "瓣", "根", "滴", "枚", "张", "毫升")
SEASONING_WORDS = ("糖", "盐", "酱油", "醋", "油", "料酒", "胡椒", "辣椒", "蒜", "姜", "葱")
INGREDIENT_ALIASES = {"熟芝麻": "芝麻", "白芝麻": "芝麻", "白砂糖": "糖"}
DIRECT_CHATGPT_FILENAME = "project-dashboard-recipe-chatgpt-direct-20260910.json"
NAS_EXPORT_RELATIVE_PATH = Path("raw/docs/nas-recipes/nas-recipes-import-20260910.json")
MEMORY_ROOT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")

def _stable_id(title: str, variant: str = "", ingredients: list[dict[str, Any]] | None = None, steps: list[str] | None = None) -> str:
    normalized = re.sub(r"\s+", "", f"{title}|{variant}|{json.dumps(ingredients or [], ensure_ascii=False, sort_keys=True)}|{'|'.join(steps or [])}").lower()
    return "recipe-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _ingredient_role(name: str) -> str:
    return "调料" if any(word in name for word in SEASONING_WORDS) else "主料"


def _format_amount(amount: int | float) -> str:
    """Render numeric evidence without corrupting integer trailing zeroes."""
    if isinstance(amount, int):
        return str(amount)
    return format(amount, ".15g")


def _ingredient(value: Any) -> dict[str, Any] | None:
    """Normalize old string entries and the fixed recipe-template object."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        match = re.match(rf"^(\d+(?:\.\d+)?)\s*({'|'.join(AMOUNT_UNITS)})\s+(.+)$", text)
        amount, unit, name = (float(match.group(1)), match.group(2), match.group(3).strip()) if match else (None, "待补充", text)
        recorded = amount is not None and math.isfinite(amount) and amount > 0
        return {"name": name, "role": _ingredient_role(name), "group": "", "amount": amount if recorded else None, "quantity_text": f"{match.group(1)} {unit}" if match else text, "unit": unit if recorded else "待补充", "package_spec": None, "amount_status": "recorded" if recorded else "pending"}
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip()
    if not name:
        return None
    raw_amount = value.get("amount")
    quantity_text = str(value.get("quantity_text") or "").strip()
    amount = raw_amount
    if isinstance(raw_amount, str):
        raw_amount = raw_amount.strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", raw_amount):
            amount = float(raw_amount)
        else:
            amount = None
            quantity_text = quantity_text or raw_amount
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount) or amount <= 0:
        amount = None
    unit = str(value.get("unit") or "").strip() or "待补充"
    package_spec = str(value.get("package_spec") or "").strip() or None
    role = str(value.get("role") or "").strip()
    requested_status = str(value.get("amount_status") or "").strip()
    # Only an exact positive number is numeric evidence. Ranges, approximations
    # and 少许 remain text so the UI never silently turns them into a quantity.
    amount_status = "recorded" if requested_status != "pending" and amount is not None and unit in AMOUNT_UNITS else "pending"
    return {"name": name, "role": role if role in {"主料", "调料"} else _ingredient_role(name), "group": str(value.get("group") or "").strip(), "amount": amount, "quantity_text": quantity_text or (_format_amount(amount) if amount is not None else ""), "unit": unit, "package_spec": package_spec, "amount_status": amount_status}


def _method_status(item: dict[str, Any], steps: list[str]) -> str:
    # Legacy reports only contain fragments. They cannot be shown as a complete recipe.
    return "complete" if item.get("method_status") == "complete" and steps else "pending"

def _json_section(text: str) -> list[dict[str, Any]]:
    section = markdown_section(text, [RECIPE_HEADING])
    match = re.search(r"```json\s*(\[.*?\])\s*```", section, re.S)
    if not match:
        return []
    try: value = json.loads(match.group(1))
    except json.JSONDecodeError: return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

def _source_ref(source: Path, raw: Path, date: str, title: str, session_id: str, excerpt: str) -> dict[str, str]:
    return {"source_date": date, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(), "session_title": title, "session_id": session_id, "evidence_excerpt": excerpt}

def _candidate(item: dict[str, Any], source: Path, raw: Path, date: str) -> dict[str, Any] | None:
    title, variant = str(item.get("title") or "").strip(), str(item.get("variant") or "").strip()
    session_id, session_title = str(item.get("session_id") or "").strip(), str(item.get("session_title") or "").strip()
    excerpt = str(item.get("evidence_excerpt") or "").strip()
    raw_text = raw.read_text(encoding="utf-8")
    heading = re.search(rf"^###\s+{re.escape(session_id)}\s+[—-]\s+(.+?)\s*$", raw_text, re.M)
    session_text = re.split(r"^###\s+S\d+\s+[—-]", raw_text[heading.end():], maxsplit=1, flags=re.M)[0] if heading else ""
    if not title or not re.fullmatch(r"S\d{2,}", session_id) or not heading or heading.group(1).strip() != session_title or not excerpt or excerpt not in session_text: return None
    raw_ingredients, raw_steps = item.get("ingredients") or [], item.get("steps") or []
    if not isinstance(raw_ingredients, list) or not isinstance(raw_steps, list) or not all(isinstance(v, str) for v in raw_steps): return None
    ingredients = [ingredient for ingredient in (_ingredient(v) for v in raw_ingredients) if ingredient]
    steps = [v.strip() for v in raw_steps if v.strip()]
    method_status = _method_status(item, steps)
    ingredients_complete = bool(ingredients) and all(ingredient["amount_status"] == "recorded" for ingredient in ingredients)
    return {"recipe_id": _stable_id(title, variant, ingredients, steps), "title": title, "variant": variant, "ingredients": ingredients, "steps": steps, "method_status": method_status, "status": "complete" if ingredients_complete and method_status == "complete" else "needs_completion", "source_refs": [_source_ref(source, raw, date, session_title, session_id, excerpt)]}

def _legacy_candidates(source: Path, raw: Path, date: str, memory_root: Path) -> list[dict[str, Any] | None]:
    """Finite initial backfill; a changed source/raw hash disables its entry."""
    path = memory_root / "wiki/project-dashboard-recipe-backfill.json"
    try: entries = json.loads(path.read_text(encoding="utf-8")).get("entries") or []
    except (OSError, json.JSONDecodeError): return []
    source_hash, raw_hash = hashlib.sha256(source.read_bytes()).hexdigest(), hashlib.sha256(raw.read_bytes()).hexdigest()
    return [_candidate(item, source, raw, date) for item in entries if isinstance(item, dict) and item.get("source_date") == date and item.get("source_sha256") == source_hash and item.get("raw_sha256") == raw_hash]

def _direct_candidate(item: dict[str, Any]) -> dict[str, Any] | None:
    title = str(item.get("title") or "").strip()
    raw_ingredients, raw_steps = item.get("ingredients") or [], item.get("steps") or []
    refs = [ref for ref in item.get("source_refs") or [] if isinstance(ref, dict)]
    if not title or not isinstance(raw_ingredients, list) or not isinstance(raw_steps, list) or not refs:
        return None
    ingredients = [ingredient for ingredient in (_ingredient(value) for value in raw_ingredients) if ingredient]
    steps = [str(value).strip() for value in raw_steps if str(value).strip()]
    source_refs = [{
        "source_type": str(ref.get("source_type") or "chatgpt_direct"),
        "source_name": str(ref.get("conversation_title") or "ChatGPT 原始会话"),
        "source_url": str(ref.get("conversation_url") or ""),
        "source_date": str(ref.get("source_date") or ""),
        "evidence_excerpt": str(ref.get("evidence") or ""),
    } for ref in refs]
    variant = str(item.get("variant") or "").strip()
    method_status = _method_status(item, steps)
    ingredients_complete = bool(ingredients) and all(ingredient["amount_status"] == "recorded" for ingredient in ingredients)
    return {"recipe_id": _stable_id(title, variant, ingredients, steps), "title": title, "variant": variant,
            "ingredients": ingredients, "steps": steps, "method_status": method_status,
            "status": "complete" if ingredients_complete and method_status == "complete" else "needs_completion", "source_refs": source_refs, "notes": []}

def _notes_ingredients(notes: str) -> list[dict[str, Any]]:
    """Extract recipe-formula fragments, including quoted Bitable annotation text."""
    found: list[dict[str, Any]] = []
    group = ""
    for fragment in _note_text_fragments(notes):
      for segment in re.split(r"[|｜]", fragment):
        if re.search(r"(?:Bitable source|来源|原始食材文本)", segment, re.I):
            continue
        if re.match(r"\s*\d+(?:\.\d+)?\s*(?:g|mL|ml)?\s*=", segment):
            continue
        # An annotation can state the total energy before a parenthesized
        # recipe. Only its explicit ingredient formula is evidence here.
        formula = re.search(r"(?:完整配方版本)?\s*=\s*\d+(?:\.\d+)?\s*kcal\s*[（(]([^）)]+)[）)]", segment, re.I)
        if formula:
            segment = formula.group(1)
        segment = re.sub(r"\s*=\s*\d+(?:\.\d+)?\s*kcal.*$", "", segment, flags=re.I)
        segment = re.split(r"[，,]\s*(?:整份|P\d|F\d|C\d)", segment, maxsplit=1, flags=re.I)[0]
        label, body = "", segment
        match = re.match(r"\s*([^:：]{1,24})[:：]\s*(.*)", segment)
        if match:
            label, body = match.group(1).strip(), match.group(2)
            if label and not re.search(r"(?:source|来源|原始食材文本|整份|热量|kcal)", label, re.I): group = label
        for name, quantity, unit in re.findall(r"([^+，,；;|｜:：()（）\d]{1,24}?)\s*(~?\d+(?:\.\d+)?(?:\s*[-–—]\s*\d+(?:\.\d+)?)?|少许|适量)\s*(kg|g|mL|ml|L|l|毫升|个|只|瓣|根|包|勺|片)?", body):
            name = name.strip().lstrip("、 ")
            if not name or (not unit and quantity not in {"少许", "适量"}):
                continue
            found.append(_ingredient({"name": name, "amount": quantity, "unit": unit or "", "group": group}) or {})
    return [item for item in found if item]


def _note_text_fragments(notes: str) -> list[str]:
    """Decode textual Bitable payloads instead of regex-scanning their repr."""
    fragments: list[str] = []
    occupied: list[tuple[int, int]] = []
    for match in re.finditer(r"\[\{.*?\}\]", notes or ""):
        try:
            payload = ast.literal_eval(match.group(0))
        except (SyntaxError, ValueError):
            continue
        if isinstance(payload, list):
            fragments.extend(str(item.get("text") or "") for item in payload if isinstance(item, dict) and isinstance(item.get("text"), str))
            occupied.append(match.span())
    plain = notes or ""
    for start, end in reversed(occupied):
        plain = plain[:start] + plain[end:]
    fragments.extend(part.strip() for part in re.split(r"[|｜]", plain) if part.strip())
    return fragments

def _dedupe_ingredients(ingredients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one copy of identical evidence while retaining genuinely conflicting entries."""
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in ingredients:
        quantity = item["quantity_text"] or (_format_amount(item["amount"]) if item["amount"] is not None else "")
        key = (INGREDIENT_ALIASES.get(item["name"], item["name"]), item["group"], quantity, item["unit"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped

def _notes_steps(notes: str) -> list[str]:
    """Keep explicitly written cooking instructions, never nutrition/source metadata."""
    if not notes or re.search(r"^\s*(?:Bitable source|原始食材文本)", notes, re.I):
        return []
    actions = r"切(?:丝|块|片)?|打散|炒|煮|蒸|拌|腌制|冷藏|放入|加水|铺底|入锅|慢煮|食用|烹|烤|焯|炖|煎"
    parts = [part.strip(" ；;。\n") for part in re.split(r"[。；;\n]+|(?=\s*\d+\.\s*)", notes) if part.strip(" ；;。\n")]
    return [part for part in parts if not re.search(r"(?:Bitable source|来源|原始食材文本|kcal|热量|整份|\b[PCF]\d)", part, re.I) and re.search(actions, part)]

def _nas_candidates(memory_root: Path = MEMORY_ROOT) -> list[dict[str, Any]]:
    try:
        source = memory_root / NAS_EXPORT_RELATIVE_PATH
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        rows = json.loads(source.read_text(encoding="utf-8")).get("recipes") or []
    except (OSError, json.JSONDecodeError):
        return []
    recipes = []
    for row in rows:
        if not isinstance(row, dict) or row.get("id") in {2, 24}:
            continue
        title = str(row.get("name") or "").strip()
        if not title:
            continue
        try: stored = json.loads(row.get("ingredients_json") or "[]")
        except (TypeError, json.JSONDecodeError): stored = []
        ingredients = [_ingredient({"name": item.get("food"), "amount": item.get("weight_g"), "unit": "g"}) for item in stored if isinstance(item, dict)]
        notes = str(row.get("notes") or "").strip()
        ingredients.extend(_notes_ingredients(notes))
        ingredients = _dedupe_ingredients([item for item in ingredients if item])
        row_id = int(row["id"])
        steps = _notes_steps(notes)
        base_names = {INGREDIENT_ALIASES.get(item["name"], item["name"]) for item in ingredients if not item["group"]}
        grouped_names = {INGREDIENT_ALIASES.get(item["name"], item["name"]) for item in ingredients if item["group"]}
        overlap = base_names & grouped_names
        conflicting = [item for item in ingredients if not item["group"] and INGREDIENT_ALIASES.get(item["name"], item["name"]) in overlap]
        conflict_note = ""
        if conflicting:
            historical = "、".join(item["name"] + " " + item["quantity_text"] + " " + item["unit"] for item in conflicting)
            conflict_note = "来源用量存在版本差异，需核对后再做。下方食材清单采用备注中的分组调料；数据库另记：" + historical + "。这些历史用量仅供核对，不要额外加入。"
            ingredients = [item for item in ingredients if item not in conflicting]
        recipes.append({"recipe_id": "nas-recipe-" + str(row_id), "title": title, "variant": "", "ingredients": ingredients,
                        "steps": steps, "method_status": "partial" if steps else "pending", "status": "needs_completion",
                        "source_refs": [{"source_type": "nas_nutrition_recipe", "source_name": "NAS Nutrition Tracker", "source_url": "", "source_path": str(NAS_EXPORT_RELATIVE_PATH), "source_sha256": source_hash, "source_date": str(row.get("updated_at") or "")[:10], "source_row_id": row_id}],
                        "notes": ([conflict_note] if conflict_note else []) + ([notes] if notes else [])})
    return recipes

def build_recipe_collection(*, source_root: Path, memory_root: Path) -> list[dict[str, Any]]:
    """Read receipt-bound daily candidates plus explicit direct and NAS imports."""
    merged: dict[str, dict[str, Any]] = {}
    for source in sorted(source_root.glob("chatgpt-daily/*/chatgpt-daily-report-*.md")):
        text, date = source.read_text(encoding="utf-8"), ""
        date, raw_rel = frontmatter_value(text, "date"), frontmatter_value(text, "raw_source")
        expected = f"raw/conversations/chatgpt-daily/{date[:4]}/chatgpt-daily-report-{date}.md"
        raw = memory_root / raw_rel
        if raw_rel != expected or not raw.is_file() or not source_deposition_state(source, memory_root)["ready"]: continue
        raw_text = raw.read_text(encoding="utf-8")
        if frontmatter_value(raw_text,"type") != "chatgpt_daily_report" or frontmatter_value(raw_text,"date") != date: continue
        candidates = [_candidate(item,source,raw,date) for item in _json_section(text)]
        # The finite initial backfill is separately curated from exact receipt-
        # bound sources. New dates require the explicit Recipe Candidates block.
        candidates.extend(_legacy_candidates(source,raw,date,memory_root))
        for recipe in (item for item in candidates if item):
            existing = merged.get(recipe["recipe_id"])
            if existing is None: merged[recipe["recipe_id"]] = recipe; continue
            existing["source_refs"].extend(ref for ref in recipe["source_refs"] if ref not in existing["source_refs"])
    direct_path = memory_root / "wiki" / DIRECT_CHATGPT_FILENAME
    if direct_path.is_file():
        try: direct_recipes = json.loads(direct_path.read_text(encoding="utf-8")).get("recipes") or []
        except json.JSONDecodeError: direct_recipes = []
        for item in direct_recipes:
            recipe = _direct_candidate(item) if isinstance(item, dict) else None
            if recipe: merged.setdefault(recipe["recipe_id"], recipe)
    for recipe in _nas_candidates(memory_root):
        merged.setdefault(recipe["recipe_id"], recipe)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in merged.values():
        groups.setdefault((item["title"], item["variant"]), []).append(item)
    for (_, variant), versions in groups.items():
        if len(versions) > 1:
            for item in versions:
                item["variant"] = (variant + " · " if variant else "") + "版本 " + item["recipe_id"][-6:]
    return sorted(merged.values(), key=lambda item:(max((ref.get("source_date") or "" for ref in item["source_refs"]), default=""),item["title"]), reverse=True)

def rebuild_recipe_registry(*, source_root: Path, memory_root: Path, output: Path) -> dict[str, Any]:
    recipes = build_recipe_collection(source_root=source_root, memory_root=memory_root)
    payload = {"artifact_schema_version":"2.0","artifact_type":"project_dashboard_recipe_registry","classification":"derived_view","recipes":recipes}
    rendered = json.dumps(payload,ensure_ascii=False,indent=2)+"\n"
    if output.is_file() and output.read_text(encoding="utf-8") == rendered: return payload
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.write_text(rendered,encoding="utf-8"); os.replace(temporary,output)
    return payload
