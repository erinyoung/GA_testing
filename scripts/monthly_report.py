#!/usr/bin/env python3
"""
StaPH-B docker-builds Monthly Report Generator

Inputs:
  1. Docker Hub comparison CSV (e.g., 'docker report - 2026-10-01.csv')
  2. Quay.io weekly audit log JSON (e.g., 'data.json')
  3. Deployed images/tags (plain text file or automated via GitHub Actions API)

Outputs:
  - report.txt         (Plaintext summary matching your monthly post template)
  - docker_report.png  (16:9 dark-mode visual infographic card)
  - REPORT.md          (GitHub/GitLab formatted Markdown document)
"""

import argparse
from collections import Counter
from datetime import datetime
import json
import os
import re
import urllib.request
import matplotlib.gridspec as gridspec
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import pandas as pd


# =====================================================================
# 1. PARSE DOCKER HUB CSV
# =====================================================================
def parse_dockerhub_csv(csv_path):
    """
    Parses the multi-column Docker Hub monthly comparison CSV.
    Extracts current & prior pull counts, calculates monthly deltas,
    and identifies newly added images.
    """
    df_raw = pd.read_csv(csv_path, header=None)

    # Extract date from cell A1 (e.g., "Docker pulls 2026-10-01:")
    header_str = str(df_raw.iloc[0, 0])
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", header_str)
    report_date_str = date_match.group(0) if date_match else "Current Month"
    try:
        report_date = datetime.strptime(report_date_str, "%Y-%m-%d")
        month_label = report_date.strftime("%B %Y")
    except Exception:
        month_label = report_date_str

    # Data starts at row index 2
    # Col 0: Image (Current), Col 1: pulls_to_date, Col 2: tags
    # Col 4: Image (Prior),   Col 5: pulls_to_date, Col 6: tags
    # Col 11: Num pulls delta (if precomputed)
    data = df_raw.iloc[2:].copy()
    data = data[data[0].notna() & (data[0].astype(str).str.strip() != "")]

    data["curr_name"] = data[0].astype(str).str.strip()
    data["curr_pulls"] = pd.to_numeric(data[1], errors="coerce").fillna(0)
    data["curr_tags"] = pd.to_numeric(data[2], errors="coerce").fillna(0)

    data["prior_name"] = data[4].astype(str).str.strip()
    data["prior_pulls"] = pd.to_numeric(data[5], errors="coerce").fillna(0)
    data["prior_tags"] = pd.to_numeric(data[6], errors="coerce").fillna(0)

    # Monthly pull calculation: column 11 or delta (curr - prior)
    if data.shape[1] > 11 and data[11].notna().any():
        data["month_pulls"] = pd.to_numeric(data[11], errors="coerce").fillna(
            data["curr_pulls"] - data["prior_pulls"]
        )
    else:
        data["month_pulls"] = data["curr_pulls"] - data["prior_pulls"]

    # All-time top 5 images
    top5_overall = data.sort_values(by="curr_pulls", ascending=False).head(5)[
        ["curr_name", "curr_pulls"]
    ]
    top5_overall_list = [
        (row["curr_name"], int(row["curr_pulls"]))
        for _, row in top5_overall.iterrows()
    ]

    # Prior month top 5 images
    top5_monthly = data.sort_values(by="month_pulls", ascending=False).head(5)[
        ["curr_name", "month_pulls"]
    ]
    top5_monthly_list = [
        (row["curr_name"], int(row["month_pulls"]))
        for _, row in top5_monthly.iterrows()
    ]

    # Totals and image catalog discovery
    total_pulls = int(data["curr_pulls"].sum())
    new_pulls = int(data["month_pulls"].sum())
    total_images = len(data)

    curr_images_set = set(data["curr_name"])
    prior_images_set = set(
        data[data["prior_name"] != "nan"]["prior_name"]
    ) - {""}
    new_images = sorted(list(curr_images_set - prior_images_set))

    return {
        "month_label": month_label,
        "total_pulls": total_pulls,
        "new_pulls": new_pulls,
        "total_images": total_images,
        "new_images_count": len(new_images),
        "new_images_list": new_images,
        "top5_overall": top5_overall_list,
        "top5_monthly": top5_monthly_list,
    }


# =====================================================================
# 2. PARSE QUAY.IO AUDIT LOGS JSON
# =====================================================================
def parse_quay_json(json_path):
    """
    Parses Quay.io audit logs JSON export (data['logs'])
    and counts occurrences of each repository to return the top 5.
    """
    with open(json_path, "r") as f:
        data = json.load(f)

    # Handles Quay raw audit log output
    if isinstance(data, dict) and "logs" in data:
        repo_counts = Counter(
            log.get("metadata", {}).get("repo")
            for log in data["logs"]
            if log.get("metadata", {}).get("repo")
        )
        return repo_counts.most_common(5)

    # Fallback if a pre-aggregated dictionary is passed: {"repo": count}
    elif isinstance(data, dict):
        return sorted(
            [(k, int(v)) for k, v in data.items()],
            key=lambda x: x[1],
            reverse=True,
        )[:5]

    return []


# =====================================================================
# 3. GET DEPLOYMENTS (FILE OR GITHUB ACTIONS API)
# =====================================================================
def get_deployed_packages(text_file=None):
    """
    Loads deployment entries from a plain text file if provided.
    Otherwise queries GitHub's public Actions workflow API for StaPH-B/docker-builds.
    """
    if text_file and os.path.exists(text_file):
        with open(text_file, "r") as f:
            lines = [line.strip() for line in f if line.strip()]
        return lines

    # Automated query to GitHub Actions deploy.yml runs
    url = "https://api.github.com/repos/StaPH-B/docker-builds/actions/workflows/deploy.yml/runs?status=success&per_page=60"
    req = urllib.request.Request(
        url, headers={"User-Agent": "StaPH-B-Monthly-Reporter"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode())
            runs = payload.get("workflow_runs", [])
            deploy_lines = []
            for r in runs:
                name = r.get("name", "")
                if name.startswith("Deploy "):
                    deploy_lines.append(name.replace("Deploy ", "").strip())
            if deploy_lines:
                return sorted(list(set(deploy_lines)))
    except Exception:
        pass

    return []


# =====================================================================
# 4. GENERATE INFOGRAPHIC CARD (PNG)
# =====================================================================
def generate_infographic(
    hub_data, quay_data, deployments, out_file="docker_report.png"
):
    """
    Renders a 16:9 dark-mode visual status card using Matplotlib.
    """
    plt.rcParams["font.sans-serif"] = "DejaVu Sans"
    fig = plt.figure(figsize=(16, 10.5), facecolor="#0f172a", dpi=180)
    gs = gridspec.GridSpec(
        4,
        1,
        figure=fig,
        height_ratios=[0.55, 0.85, 2.3, 0.8],
        hspace=0.32,
        left=0.06,
        right=0.94,
        top=0.94,
        bottom=0.05,
    )

    # Header section
    ax_title = fig.add_subplot(gs[0])
    ax_title.set_facecolor("#0f172a")
    ax_title.axis("off")
    ax_title.text(
        0.0,
        0.75,
        "StaPH-B / docker-builds",
        fontsize=26,
        fontweight="bold",
        color="#38bdf8",
        va="top",
    )
    ax_title.text(
        0.0,
        0.20,
        f"Community Usage Metrics & Release Snapshot • {hub_data['month_label']}",
        fontsize=12.5,
        color="#94a3b8",
        va="top",
    )

    # KPI stat cards
    top_monthly_tool = (
        hub_data["top5_monthly"][0][0] if hub_data["top5_monthly"] else "N/A"
    )
    top_monthly_val = (
        f"{hub_data['top5_monthly'][0][1]:,} pulls"
        if hub_data["top5_monthly"]
        else ""
    )
    quay_leader = quay_data[0][0] if quay_data else "N/A"
    quay_leader_val = (
        f"{quay_data[0][1]:,} weekly pulls" if quay_data else "N/A"
    )

    kpis = [
        (
            "TOTAL DOCKER HUB PULLS",
            f"{hub_data['total_pulls']/1e6:.2f}M",
            f"+{hub_data['new_pulls']:,} this month",
            "#38bdf8",
        ),
        (
            "ACTIVE CONTAINER CATALOG",
            f"{hub_data['total_images']} Images",
            f"+{hub_data['new_images_count']} new tools!",
            "#34d399",
        ),
        ("TOP MONTHLY TOOL", top_monthly_tool, top_monthly_val, "#f472b6"),
        ("QUAY.IO TOP PERFORMER", quay_leader, quay_leader_val, "#fbbf24"),
    ]

    gs_kpis = gridspec.GridSpecFromSubplotSpec(1, 4, subplot_spec=gs[1], wspace=0.15)
    for i, (title, val, sub, col) in enumerate(kpis):
        ax_k = fig.add_subplot(gs_kpis[0, i])
        ax_k.set_facecolor("#1e293b")
        ax_k.axis("off")
        card = patches.FancyBboxPatch(
            (0.02, 0.05),
            0.96,
            0.9,
            boxstyle="round,pad=0.04,rounding_size=0.12",
            facecolor="#1e293b",
            edgecolor=col,
            linewidth=1.8,
            transform=ax_k.transAxes,
        )
        ax_k.add_patch(card)
        ax_k.text(
            0.08,
            0.74,
            title,
            fontsize=8.5,
            fontweight="bold",
            color="#94a3b8",
            transform=ax_k.transAxes,
        )
        ax_k.text(
            0.08,
            0.40,
            val,
            fontsize=18,
            fontweight="bold",
            color="#f8fafc",
            transform=ax_k.transAxes,
        )
        ax_k.text(
            0.08,
            0.17,
            sub,
            fontsize=8.5,
            fontweight="semibold",
            color=col,
            transform=ax_k.transAxes,
        )

    # 3 horizontal bar charts
    gs_charts = gridspec.GridSpecFromSubplotSpec(
        1, 3, subplot_spec=gs[2], wspace=0.25
    )

    def draw_bar(
        area, title, items, bar_color, is_millions=False, scale_factor=1.28
    ):
        ax = fig.add_subplot(area)
        ax.set_facecolor("#1e293b")
        ax.set_title(
            title, color="#e2e8f0", fontsize=11, fontweight="bold", pad=12
        )

        names = [x[0] for x in reversed(items)]
        vals = [x[1] for x in reversed(items)]
        y_pos = range(len(names))

        bars = ax.barh(
            y_pos, vals, color=bar_color, height=0.55, edgecolor="none"
        )
        ax.set_yticks(y_pos)
        ax.set_yticklabels(
            names, color="#f8fafc", fontsize=10, fontweight="semibold"
        )

        max_v = max(vals) if vals else 1
        ax.set_xlim(0, max_v * scale_factor)

        for bar in bars:
            w = bar.get_width()
            lbl = f"{w/1e6:.2f}M" if is_millions else f"{int(w):,}"
            ax.text(
                w + (max_v * 0.02),
                bar.get_y() + bar.get_height() / 2,
                lbl,
                va="center",
                ha="left",
                color="#f8fafc",
                fontsize=9,
                fontweight="bold",
            )

        ax.tick_params(colors="#94a3b8", labelsize=8.5)
        for s in ["top", "right"]:
            ax.spines[s].set_visible(False)
        for s in ["left", "bottom"]:
            ax.spines[s].set_color("#334155")
        ax.grid(axis="x", color="#334155", linestyle="--", alpha=0.4)
        if is_millions:
            ax.xaxis.set_major_formatter(
                plt.FuncFormatter(lambda x, p: f"{x/1e6:.1f}M")
            )

    draw_bar(
        gs_charts[0, 0],
        "Top Pulls (Prior Month)\nhub.docker.com/u/staphb",
        hub_data["top5_monthly"],
        "#38bdf8",
    )
    draw_bar(
        gs_charts[0, 1],
        "Top 5 Overall Downloads\nAll-Time Docker Hub",
        hub_data["top5_overall"],
        "#34d399",
        is_millions=True,
    )
    draw_bar(
        gs_charts[0, 2],
        "Quay.io Top Pulls\nPast Week (quay.io/staphb)",
        quay_data,
        "#fbbf24",
    )

    # Footer banner
    ax_foot = fig.add_subplot(gs[3])
    ax_foot.set_facecolor("#1e293b")
    ax_foot.axis("off")
    foot_box = patches.FancyBboxPatch(
        (0.0, 0.0),
        1.0,
        1.0,
        boxstyle="round,pad=0.03,rounding_size=0.08",
        facecolor="#1e293b",
        edgecolor="#475569",
        linewidth=1.2,
        transform=ax_foot.transAxes,
    )
    ax_foot.add_patch(foot_box)

    new_tools_str = (
        ", ".join(hub_data["new_images_list"])
        if hub_data["new_images_list"]
        else "None"
    )
    ax_foot.text(
        0.02,
        0.74,
        f"DEPLOYMENT DIGEST: {len(deployments)} BUILDS / TAGS DEPLOYED",
        fontsize=10,
        fontweight="bold",
        color="#a78bfa",
        transform=ax_foot.transAxes,
    )
    ax_foot.text(
        0.02,
        0.44,
        f"[★] Brand New Tools Added: {new_tools_str}",
        fontsize=9.5,
        fontweight="bold",
        color="#34d399",
        transform=ax_foot.transAxes,
    )
    ax_foot.text(
        0.02,
        0.17,
        f"Deployment details updated automatically for {hub_data['month_label']}",
        fontsize=9,
        color="#94a3b8",
        transform=ax_foot.transAxes,
    )

    plt.savefig(out_file, dpi=200, bbox_inches="tight")
    plt.close()


# =====================================================================
# 5. GENERATE TEXT & MARKDOWN
# =====================================================================
def generate_text_and_markdown(
    hub_data,
    quay_data,
    deployments,
    txt_out="report.txt",
    md_out="REPORT.md",
    img_name="docker_report.png",
):
    """
    Writes plaintext report for copy-pasting and formatted Markdown.
    """
    txt_lines = [
        "Status report for StaPH-B/docker-builds:\n",
        "Most popular image pulls for the prior month from https://hub.docker.com/u/staphb:",
    ]
    for name, pulls in hub_data["top5_monthly"]:
        txt_lines.append(f"{name} ({pulls:,})")

    txt_lines.append("\nTop five images downloaded overall:")
    for name, pulls in hub_data["top5_overall"]:
        txt_lines.append(f"{name} ({pulls:,})")

    txt_lines.append("\nTotal numbers from dockerhub:")
    new_img_str = (
        f" ({hub_data['new_images_count']} new!)"
        if hub_data["new_images_count"] > 0
        else ""
    )
    txt_lines.append(
        f"Total pulls: {hub_data['total_pulls']:,} ({hub_data['new_pulls']:,} new)"
    )
    txt_lines.append(
        f"Total number of images: {hub_data['total_images']}{new_img_str}\n"
    )

    txt_lines.append(
        "And a snapshot with the 5 most popular image pulls from https://quay.io/organization/staphb from the prior week:"
    )
    for name, pulls in quay_data:
        txt_lines.append(f"{name}: {pulls}")

    txt_lines.append(
        f"\nNew images or old images with new tags deployed in {hub_data['month_label']}:"
    )
    for dep in deployments:
        txt_lines.append(dep)

    with open(txt_out, "w") as f:
        f.write("\n".join(txt_lines))

    # Formatted Markdown
    md_lines = [
        f"# StaPH-B / docker-builds Status Report — {hub_data['month_label']}\n",
        f"![StaPH-B Docker Metrics]({img_name})\n",
        "## Key Performance Indicators",
        "| Metric | Total | Monthly Delta |",
        "| :--- | :--- | :--- |",
        f"| **Docker Hub Pulls** | `{hub_data['total_pulls']:,}` | `+{hub_data['new_pulls']:,}` |",
        f"| **Active Catalog Images** | `{hub_data['total_images']}` | `+{hub_data['new_images_count']} new` |",
        "",
        "## Deployed Images & Tags",
    ]
    for dep in deployments:
        md_lines.append(f"- {dep}")

    with open(md_out, "w") as f:
        f.write("\n".join(md_lines))


# =====================================================================
# CLI ENTRYPOINT
# =====================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate StaPH-B docker-builds status report and infographic."
    )
    parser.add_argument(
        "--csv",
        default="docker_report.csv",
        help="Path to Docker Hub monthly comparison CSV",
    )
    parser.add_argument(
        "--quay",
        default="data.json",
        help="Path to Quay weekly audit log JSON",
    )
    parser.add_argument(
        "--deployments",
        default="deployments.txt",
        help="Path to text file containing deployed packages (optional)",
    )
    parser.add_argument(
        "--out-img", default="docker_report.png", help="Infographic PNG filename"
    )
    parser.add_argument(
        "--out-txt", default="report.txt", help="Plain text report filename"
    )
    parser.add_argument(
        "--out-md", default="REPORT.md", help="Markdown summary filename"
    )

    args = parser.parse_args()

    print(f"[*] Reading Docker Hub data from: {args.csv}")
    hub_data = parse_dockerhub_csv(args.csv)

    print(f"[*] Parsing Quay audit logs from: {args.quay}")
    try:
        quay_data = parse_quay_json(args.quay)
    except FileNotFoundError:
        print(f"[!] Warning: {args.quay} not found. Using empty list.")
        quay_data = []

    print("[*] Retrieving deployed packages...")
    deployments = get_deployed_packages(args.deployments)

    print(f"[*] Generating infographic: {args.out_img}")
    generate_infographic(
        hub_data, quay_data, deployments, out_file=args.out_img
    )

    print(f"[*] Writing text report ({args.out_txt}) and Markdown ({args.out_md})...")
    generate_text_and_markdown(
        hub_data,
        quay_data,
        deployments,
        txt_out=args.out_txt,
        md_out=args.out_md,
        img_name=args.out_img,
    )

    print("\n✓ Pipeline execution complete.")