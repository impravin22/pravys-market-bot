"""NSE sector index snapshots — daily change percent + simple direction classifier.

MarketSmith India categorises every NSE sector into one of four directional
states each session (Confirmed Uptrend / Uptrend Under Pressure / Rally
Attempt / Downtrend). We approximate that with the same four-phase classifier
used for the broad market in :mod:`core.canslim`, applied per sector index.

Yahoo ticker mapping is best-effort: the sub-sector indices are not all
exposed via stable Yahoo symbols, so missing fetches are skipped silently.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from core.canslim import classify_phase
from core.nse_data import fetch_history

logger = logging.getLogger(__name__)

# Sector indices the MarketSmith report mentions — ticker lookup verified on
# Yahoo Finance. Some Yahoo symbols are flaky; missing data is tolerated.
SECTOR_INDICES: dict[str, str] = {
    "Nifty Bank": "^NSEBANK",
    "Nifty IT": "^CNXIT",
    "Nifty Auto": "^CNXAUTO",
    "Nifty Pharma": "^CNXPHARMA",
    "Nifty FMCG": "^CNXFMCG",
    "Nifty Metal": "^CNXMETAL",
    "Nifty Realty": "^CNXREALTY",
    "Nifty Energy": "^CNXENERGY",
    "Nifty Media": "^CNXMEDIA",
    "Nifty PSU Bank": "^CNXPSUBANK",
    "Nifty Financial Services": "^CNXFIN",
    "Nifty Healthcare": "NIFTY_HEALTHCARE.NS",
    "Nifty Consumer Durables": "NIFTY_CONSR_DURBL.NS",
    "Nifty Oil & Gas": "NIFTY_OIL_GAS.NS",
    "Nifty PSE": "NIFTY_PSE.NS",
    "Nifty Private Bank": "^CNXPVTBANK",
}


@dataclass(frozen=True)
class SectorSnapshot:
    name: str
    last_close: float
    change_pct: float
    direction: str  # phase code from classify_phase


def _snapshot_one(name: str, symbol: str) -> SectorSnapshot | None:
    hist = fetch_history(symbol, period="1y")
    if hist is None:
        return None
    closes = hist.history["Close"].dropna()
    if len(closes) < 5:
        return None
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2])
    change_pct = (last / prev - 1.0) * 100.0 if prev else 0.0

    # Direction classifier reuses the four-phase logic against this index'
    # own moving averages, mirroring how MarketSmith treats each sub-sector.
    above_50 = last > float(closes.tail(min(50, len(closes))).mean())
    above_200 = last > float(closes.tail(min(200, len(closes))).mean())
    five_up = bool(closes.iloc[-1] / closes.iloc[-5] - 1.0 > 0)
    direction = classify_phase(above_50dma=above_50, above_200dma=above_200, five_day_up=five_up)

    return SectorSnapshot(name=name, last_close=last, change_pct=change_pct, direction=direction)


def fetch_sector_snapshots(parallelism: int = 6) -> list[SectorSnapshot]:
    """Fetch all sectors in parallel; sort by change_pct descending."""
    out: list[SectorSnapshot] = []
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {
            pool.submit(_snapshot_one, name, sym): name for name, sym in SECTOR_INDICES.items()
        }
        for fut in as_completed(futures):
            try:
                snap = fut.result()
                if snap is not None:
                    out.append(snap)
            except Exception as exc:  # noqa: BLE001
                logger.warning("sector snapshot %s failed: %s", futures[fut], exc)
    return sorted(out, key=lambda s: s.change_pct, reverse=True)
