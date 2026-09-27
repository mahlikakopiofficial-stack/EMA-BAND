def weighted_average_entry(lots):
    quantity = sum(lot.qty for lot in lots)
    if quantity <= 0:
        return 0.0
    return sum(lot.entry_price * lot.qty for lot in lots) / quantity


def liquidation_exit_price(avg_entry_price, liq_price, buffer_pct):
    """Return the price at which the configured liquidation cushion is used."""
    return liq_price + (buffer_pct / 100.0) * (avg_entry_price - liq_price)


def estimated_liquidation_price(avg_entry_price, leverage, maintenance_margin_rate):
    return avg_entry_price * (
        1.0 - 1.0 / leverage + maintenance_margin_rate
    )
