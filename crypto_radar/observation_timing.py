"""Timestamp evidence only; never feed timing metadata into candidate scoring."""
import json


def candidate_features(stats, market_received_ts, candidate_computed_ts):
    if stats is None:
        return None
    return dict(stats, observation_timing={
        'market_received_ts': market_received_ts,
        'candidate_computed_ts': candidate_computed_ts,
        'version': 1})


def delivery_receipt(logger, event_id, channel, status, returned_ts):
    # A sender returning is not proof of recipient delivery/read. Avoid message IDs,
    # recipient addresses, text, exception messages and credentials in timing logs.
    logger.info('Observation timing %s', json.dumps({
        'event': 'alert_sender_returned', 'alert_event_id': event_id,
        'channel': channel, 'status': status, 'sender_returned_ts': returned_ts},
        sort_keys=True, allow_nan=False))
