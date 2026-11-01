"""Offline evaluation of root-cause analysis (M4).

Chaos runs record **snapshots**: the alerts and evidence of a real incident plus
the known cause (ground truth). The eval replays the analysis step on each
snapshot, with no live system, and scores the answers. Same prompt code as
production (``analysis/prompt.py``), so the score describes what users get.

    python -m iip.evals --provider gemini --model gemini-3.5-flash-lite
"""
