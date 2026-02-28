#!/usr/bin/env python3
"""
Seed Channel Registry Builder

Takes the user-provided list of YouTube URLs (channels + individual videos),
extracts channel IDs, deduplicates, and builds an initial seed registry.

Usage:
    python seed_channels.py              # Build seed registry
    python seed_channels.py --qualify    # Build + run qualification on each
"""

import os
import re
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# =============================================================================
# USER-PROVIDED SOURCES
# =============================================================================

# Individual videos (channel to be extracted from video metadata)
SEED_VIDEOS = [
    {"video_id": "rykYVUNbAs0", "note": "can be scrapped"},
    {"video_id": "rK3EyLmXPzQ", "note": "can be scrapped"},
    {"video_id": "Cl_xpyMkOTQ", "note": "can be scrapped"},
    {"video_id": "SciU3RCrTe0", "note": "can be scrapped"},
    {"video_id": "IZlrbGlbNjM", "note": "can be scrapped"},
    {"video_id": "-yiwunSIu6U", "note": "can be scrapped"},
    {"video_id": "c9PUhtIDVC0", "note": "can be scrapped"},
    {"video_id": "rWyXq4RdNu4", "note": "can be scrapped"},
    {"video_id": "d_hWzbF6XOM", "note": "can be scrapped"},
    {"video_id": "hNwyKEDNSR0", "note": ""},
    {"video_id": "fBw_K0iltFI", "note": ""},
    {"video_id": "AHwOMhDtnDs", "note": ""},
    {"video_id": "4htpzGlCXWw", "note": ""},
    {"video_id": "RXlRIXFjMCw", "note": ""},
    {"video_id": "89ckVvmtNNw", "note": ""},
]

# Explicit channel URLs provided by user
SEED_CHANNELS = [
    {
        "url": "https://www.youtube.com/@JAXSHERIFF/videos",
        "handle": "JAXSHERIFF",
        "note": "can be scrapped - Jacksonville Sheriff's Office",
        "expected_type": "official_pd",
    },
    {
        "url": "https://www.youtube.com/@houstonpolice/videos",
        "handle": "houstonpolice",
        "note": "can these be triaged? - Houston Police",
        "expected_type": "official_pd",
    },
    {
        "url": "https://www.youtube.com/channel/UCYa23yHE2e1yJrlYUtyEM2w",
        "channel_id": "UCYa23yHE2e1yJrlYUtyEM2w",
        "note": "can the use of force reports be triaged?",
        "expected_type": "unknown",
    },
    {
        "url": "https://www.youtube.com/channel/UC2T9FKndXhkgHLk-lKOiCrQ",
        "channel_id": "UC2T9FKndXhkgHLk-lKOiCrQ",
        "note": "can these be triaged?",
        "expected_type": "unknown",
    },
    {
        "url": "https://www.youtube.com/channel/UCWu-Puzkp8hv9eSpnWkNxjA",
        "channel_id": "UCWu-Puzkp8hv9eSpnWkNxjA",
        "note": "can these be triaged?",
        "expected_type": "unknown",
    },
    {
        "url": "https://www.youtube.com/@DenverPoliceDept/videos",
        "handle": "DenverPoliceDept",
        "note": "can these be triaged? - Denver Police",
        "expected_type": "official_pd",
    },
    {
        "url": "https://www.youtube.com/@AustinPolice/videos",
        "handle": "AustinPolice",
        "note": "can these be triaged? - Austin Police",
        "expected_type": "official_pd",
    },
    {
        "url": "https://www.youtube.com/@spdblotter/videos",
        "handle": "spdblotter",
        "note": "can these be triaged? - Seattle Police",
        "expected_type": "official_pd",
    },
    {
        "url": "https://www.youtube.com/channel/UCmzeK2lzaBSAQQr2Q0hPArw",
        "channel_id": "UCmzeK2lzaBSAQQr2Q0hPArw",
        "note": "can these be triaged?",
        "expected_type": "unknown",
    },
]


# =============================================================================
# API HELPERS
# =============================================================================

def _yt_get(url: str, params: dict) -> Optional[dict]:
    """Make a YouTube Data API GET request."""
    if not YOUTUBE_API_KEY:
        return None
    import requests
    params["key"] = YOUTUBE_API_KEY
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"  [ERROR] {e}")
        return None


def get_channel_id_from_video(video_id: str) -> dict:
    """Get channel info from a video ID."""
    data = _yt_get("https://www.googleapis.com/youtube/v3/videos", {
        "part": "snippet",
        "id": video_id,
    })
    if not data or not data.get("items"):
        return {}
    snippet = data["items"][0].get("snippet", {})
    return {
        "channel_id": snippet.get("channelId", ""),
        "channel_name": snippet.get("channelTitle", ""),
        "video_title": snippet.get("title", ""),
    }


def resolve_handle_to_channel_id(handle: str) -> str:
    """Resolve @Handle to channel ID via search."""
    data = _yt_get("https://www.googleapis.com/youtube/v3/search", {
        "part": "snippet",
        "q": handle,
        "type": "channel",
        "maxResults": 1,
    })
    if data and data.get("items"):
        return data["items"][0].get("id", {}).get("channelId", "")
    return ""


# =============================================================================
# SEED REGISTRY BUILDER
# =============================================================================

def build_seed_registry() -> dict:
    """
    Extract channel IDs from all user-provided URLs and build seed registry.
    """
    print("=" * 60)
    print("SEED CHANNEL REGISTRY BUILDER")
    print("=" * 60)

    channels = {}  # channel_id -> info

    # Step 1: Extract channels from individual videos
    print(f"\n[1/2] Extracting channels from {len(SEED_VIDEOS)} videos...")
    for i, v in enumerate(SEED_VIDEOS):
        vid = v["video_id"]
        print(f"  [{i+1}/{len(SEED_VIDEOS)}] Video {vid}...")
        info = get_channel_id_from_video(vid)
        if info and info.get("channel_id"):
            ch_id = info["channel_id"]
            if ch_id not in channels:
                channels[ch_id] = {
                    "channel_id": ch_id,
                    "channel_name": info.get("channel_name", ""),
                    "source": "video_extraction",
                    "sample_videos": [],
                    "user_note": v.get("note", ""),
                }
            channels[ch_id]["sample_videos"].append({
                "video_id": vid,
                "title": info.get("video_title", ""),
            })
            print(f"    -> {info['channel_name']} ({ch_id})")
        else:
            print(f"    -> Could not resolve (API key needed)")

    print(f"\n  Unique channels from videos: {len(channels)}")

    # Step 2: Add explicit channel URLs
    print(f"\n[2/2] Processing {len(SEED_CHANNELS)} explicit channels...")
    for i, ch in enumerate(SEED_CHANNELS):
        ch_id = ch.get("channel_id", "")
        handle = ch.get("handle", "")

        if not ch_id and handle:
            print(f"  [{i+1}] Resolving @{handle}...")
            ch_id = resolve_handle_to_channel_id(handle)
            if ch_id:
                print(f"    -> {ch_id}")
            else:
                print(f"    -> Could not resolve handle")

        if ch_id:
            if ch_id not in channels:
                channels[ch_id] = {
                    "channel_id": ch_id,
                    "channel_name": "",
                    "source": "user_provided",
                    "sample_videos": [],
                    "user_note": ch.get("note", ""),
                    "expected_type": ch.get("expected_type", "unknown"),
                }
            else:
                # Merge notes
                existing_note = channels[ch_id].get("user_note", "")
                new_note = ch.get("note", "")
                if new_note and new_note not in existing_note:
                    channels[ch_id]["user_note"] = f"{existing_note}; {new_note}".strip("; ")
        else:
            # Store with URL for manual resolution
            placeholder_id = f"UNRESOLVED_{i}"
            channels[placeholder_id] = {
                "channel_id": "",
                "channel_url": ch.get("url", ""),
                "channel_name": "",
                "source": "user_provided_unresolved",
                "user_note": ch.get("note", ""),
                "expected_type": ch.get("expected_type", "unknown"),
            }

    # Summary
    resolved = [c for c in channels.values() if c.get("channel_id")]
    unresolved = [c for c in channels.values() if not c.get("channel_id")]

    print(f"\n{'='*60}")
    print("SEED REGISTRY SUMMARY")
    print(f"{'='*60}")
    print(f"Total unique channels: {len(channels)}")
    print(f"Resolved (have channel ID): {len(resolved)}")
    print(f"Unresolved (need API): {len(unresolved)}")

    print(f"\nChannels found:")
    for ch in sorted(resolved, key=lambda x: x.get("channel_name", "")):
        name = ch.get("channel_name") or ch.get("channel_id")
        videos = len(ch.get("sample_videos", []))
        note = ch.get("user_note", "")
        print(f"  {name}")
        print(f"    ID: {ch['channel_id']}")
        if videos:
            print(f"    Sample videos: {videos}")
        if note:
            print(f"    Note: {note}")

    if unresolved:
        print(f"\nUnresolved (set YOUTUBE_API_KEY to resolve):")
        for ch in unresolved:
            print(f"  {ch.get('channel_url', 'unknown')}")
            print(f"    Note: {ch.get('user_note', '')}")

    return channels


def save_seed_registry(channels: dict):
    """Save seed registry to qualified_channels.json format."""
    from pipeline_ops import score_channel_criteria

    registry = {}
    for ch_id, ch_data in channels.items():
        if not ch_data.get("channel_id"):
            continue

        # Create a minimal registry entry
        # Full qualification will be run later with --qualify
        entry = {
            "channel_id": ch_data["channel_id"],
            "channel_name": ch_data.get("channel_name", ""),
            "channel_url": f"https://www.youtube.com/channel/{ch_data['channel_id']}",
            "subscriber_count": 0,
            "total_videos": 0,
            "last_evaluated": datetime.utcnow().isoformat() + "Z",
            "score": 0,
            "classification": "PENDING",
            "action": "Run channel_qualify.py --score to get full score",
            "score_breakdown": {},
            "channel_data": {
                "raw_footage_ratio": 0.0,
                "watermark_level": "unknown",
                "uploads_per_week": 0.0,
                "avg_duration_minutes": 0.0,
                "jurisdiction_type": "unclear",
                "source_tier": ch_data.get("expected_type", "unknown"),
                "description_quality": "none",
            },
            "needs_manual_review": True,
            "needs_full_qualification": True,
            "sample_videos": ch_data.get("sample_videos", []),
            "notes": ch_data.get("user_note", ""),
            "discovery_source": ch_data.get("source", "user_provided"),
        }

        registry[ch_data["channel_id"]] = entry

    # Save
    registry_path = Path("qualified_channels.json")
    with open(registry_path, "w") as f:
        json.dump(registry, f, indent=2)

    print(f"\nSeed registry saved to {registry_path} ({len(registry)} channels)")
    return registry


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Build seed channel registry from user-provided URLs")
    parser.add_argument("--qualify", action="store_true",
                        help="Also run full channel qualification after building seed")

    args = parser.parse_args()

    channels = build_seed_registry()

    if channels:
        registry = save_seed_registry(channels)

        if args.qualify:
            print(f"\n{'='*60}")
            print("RUNNING FULL QUALIFICATION")
            print(f"{'='*60}")
            try:
                from channel_qualify import qualify_channel, load_registry, save_registry
                full_registry = load_registry()
                for ch_id in list(full_registry.keys()):
                    if full_registry[ch_id].get("needs_full_qualification"):
                        entry = qualify_channel(ch_id)
                        if entry:
                            # Preserve user notes
                            entry["notes"] = full_registry[ch_id].get("notes", "")
                            entry["discovery_source"] = full_registry[ch_id].get("discovery_source", "")
                            full_registry[ch_id] = entry
                save_registry(full_registry)
            except Exception as e:
                print(f"Qualification error: {e}")
                print("Run manually: python channel_qualify.py --score CHANNEL_ID")


if __name__ == "__main__":
    main()
