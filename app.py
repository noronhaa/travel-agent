import os
import re
import json
from datetime import datetime, date
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_from_directory
from dotenv import load_dotenv
import markdown as md

load_dotenv()

from agent import run_agent

app = Flask(__name__)
BRIEFS_DIR = Path("briefs")
BRIEFS_DIR.mkdir(exist_ok=True)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _save_brief(params: dict, content: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = _slug(params["destination"])
    filename = f"{ts}-{dest}.md"

    meta = {
        "destination": params["destination"],
        "month": params.get("month", ""),
        "date_from": params.get("date_from", ""),
        "date_to": params.get("date_to", ""),
        "duration": params["duration"],
        "traveling_from": params.get("traveling_from", ""),
        "interests": params.get("interests", ""),
        "generated_at": datetime.now().isoformat(),
    }

    (BRIEFS_DIR / filename).write_text(
        f"<!-- meta: {json.dumps(meta)} -->\n\n{content}", encoding="utf-8"
    )
    return filename


def _list_briefs() -> list[dict]:
    briefs = []
    for f in sorted(BRIEFS_DIR.glob("*.md"), reverse=True):
        content = f.read_text(encoding="utf-8")
        meta = {}
        if content.startswith("<!-- meta:"):
            try:
                meta_line = content.split("-->")[0].replace("<!-- meta:", "").strip()
                meta = json.loads(meta_line)
            except Exception:
                pass
        period = meta.get("date_from") and meta.get("date_to") and f"{meta['date_from']} – {meta['date_to']}"
        period = period or meta.get("month", "")
        briefs.append({
            "filename": f.name,
            "destination": meta.get("destination", f.stem),
            "period": period,
            "duration": meta.get("duration", ""),
            "generated_at": meta.get("generated_at", ""),
        })
    return briefs


@app.route("/")
def index():
    return render_template("index.html", briefs=_list_briefs())


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json()

    destination = data.get("destination", "").strip()
    duration = data.get("duration", "").strip()
    month = data.get("month", "").strip()
    date_from = data.get("date_from", "").strip()
    date_to = data.get("date_to", "").strip()

    if not destination:
        return jsonify({"error": "Destination is required"}), 400
    if not duration:
        return jsonify({"error": "Duration is required"}), 400
    if not month and not (date_from and date_to):
        return jsonify({"error": "Either a month or a date range is required"}), 400

    params = {
        "destination": destination,
        "duration": duration,
        "month": month,
        "date_from": date_from,
        "date_to": date_to,
        "traveling_from": data.get("traveling_from", "").strip(),
        "interests": data.get("interests", "").strip(),
    }

    try:
        brief_md = run_agent(params)
        filename = _save_brief(params, brief_md)
        brief_html = md.markdown(brief_md, extensions=["tables", "fenced_code"])
        return jsonify({"filename": filename, "html": brief_html, "markdown": brief_md})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/brief/<filename>")
def view_brief(filename):
    filepath = BRIEFS_DIR / filename
    if not filepath.exists():
        return "Brief not found", 404

    raw = filepath.read_text(encoding="utf-8")
    meta = {}
    if raw.startswith("<!-- meta:"):
        try:
            meta_line = raw.split("-->")[0].replace("<!-- meta:", "").strip()
            meta = json.loads(meta_line)
        except Exception:
            pass
        content = raw.split("-->", 1)[1].strip()
    else:
        content = raw

    brief_html = md.markdown(content, extensions=["tables", "fenced_code"])
    return render_template("brief.html", html=brief_html, meta=meta, filename=filename)


@app.route("/brief/<filename>/raw")
def raw_brief(filename):
    return send_from_directory(BRIEFS_DIR, filename, mimetype="text/plain")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
