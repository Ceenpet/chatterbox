"""Playback scheduling only: preserve model cadence and original punctuation."""
PLAYBACK_SPEED = 1.0


def boundary_pause(decision):
    if decision.get('is_final'):
        return 0.
    return {'sentence': .35, 'clause': .30, 'discourse_comma': .30}.get(decision.get('boundary'), 0.)


def startup_hold(record, strategy):
    """Small cancellable pre-roll; GPU keeps producing while device is paused.

    First-call ratio is conservative, not a throughput guarantee. Bound the
    extra latency to 3s; don't delay complete one-segment answers or fast mode.
    """
    if strategy == 'rapido' or record['segmentation']['is_final']:
        return 0.
    ratio = record['synthesis_seconds']/record['speech_segment_seconds']
    hold = min(3., .6 + 8*max(0., ratio-1.))
    # A tiny introductory unit followed by a long sentence was responsible for
    # the known multi-second gap. Move that wait before speech in calm mode.
    next_chars = record['segmentation'].get('next_natural_unit_chars', 0)
    if record['speech_segment_seconds'] < 4 and next_chars > 2*len(record['text']):
        estimated = record['synthesis_seconds']*next_chars/max(1,len(record['text']))
        hold = min(12., max(hold, estimated-record['played_voice_seconds']+.6))
    return hold
