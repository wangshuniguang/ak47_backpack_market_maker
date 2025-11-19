#!/usr/bin/env python

from dataclasses import dataclass


@dataclass
class TradingConfig:
    """Configuration class for trading parameters."""
    ticker: str
    public_key: str
    secret_key: str
