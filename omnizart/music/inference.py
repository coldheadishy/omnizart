import math

import pretty_midi
import numpy as np
from scipy.interpolate import CubicSpline
from scipy.signal import find_peaks
from librosa import note_to_midi

from omnizart.music.utils import roll_down_sample, down_sample
from omnizart.constants.midi import MUSICNET_INSTRUMENT_PROGRAMS, MIDI_PROGRAM_NAME_MAPPING


def infer_pitch(pitch, shortest=5, offset_interval=6):
    w_on = pitch[:, 2]
    w_dura = pitch[:, 1]

    peaks, properties = find_peaks(w_on, distance=shortest, width=5)
    if len(peaks) == 0:
        return []

    notes = []
    adjust = 5 if shortest == 10 else 2
    for i in range(len(peaks) - 1):
        notes.append({"start": peaks[i] - adjust, "end": peaks[i + 1] - adjust, "stren": pitch[peaks[i], 2]})
    notes.append({"start": peaks[-1] - adjust, "end": len(w_on), "stren": pitch[peaks[-1], 2]})

    del_idx = []
    for idx, p in enumerate(peaks):
        upper = int(peaks[idx + 1]) if idx < len(peaks) - 2 else len(w_dura)
        for i in range(p, upper):
            if np.sum(w_dura[i : i + offset_interval]) == 0:
                if i - notes[idx]["start"] < shortest:
                    del_idx.append(idx)
                else:
                    notes[idx]["end"] = i
                break

    for ii, i in enumerate(del_idx):
        del notes[i - ii]

    return notes


def infer_piece(piece, shortest_sec=0.1, offset_sec=0.12, t_unit=0.02):
    """
        Dim: time x 88 x 4 (off, dura, onset, offset)
    """
    assert piece.shape[1] == 88, "Please down sample the pitch to 88 first (current: {}).format(piece.shape[1])"
    min_align_diff = 1  # to align the onset between notes with a short time difference

    notes = []
    for i in range(88):
        print("Pitch: {}/{}".format(i + 1, 88), end="\r")

        pitch = piece[:, i]
        if np.sum(pitch) <= 0:
            continue

        pns = infer_pitch(pitch, shortest=round(shortest_sec / t_unit), offset_interval=round(offset_sec / t_unit))
        for ns in pns:
            ns["pitch"] = i
            notes.append(ns)
    print(" " * 80, end="\r")

    notes = sorted(notes, key=lambda d: d["start"])
    last_start = 0
    for i in range(len(notes)):
        start_diff = notes[i]["start"] - last_start
        if start_diff < min_align_diff:
            notes[i]["start"] -= start_diff
            notes[i]["end"] -= start_diff
        else:
            last_start = notes[i]["start"]

    return notes


def find_min_max_stren(notes):
    MIN = 999
    MAX = 0
    for nn in notes:
        nn_s = nn["stren"]
        if nn_s > MAX:
            MAX = nn_s
        if nn_s < MIN:
            MIN = nn_s

    return MIN, MAX


def find_occur(pitch, t_unit=0.02, min_duration=0.03):
    """Find the onset and offset of a thresholded frame-level prediction."""

    min_duration = max(t_unit, min_duration)
    min_frm = min_duration / t_unit

    cand = np.where(pitch > 0.5)[0]
    if len(cand) == 0:
        return []

    start = cand[0]
    last = cand[0]
    note = []
    for cidx in cand:
        if cidx - last > 1:
            if last - start >= min_frm:
                note.append({"onset": start, "offset": last})
            start = cidx
        last = cidx

    if last - start >= min_frm:
        note.append({"onset": start, "offset": last})
    return note


def to_midi(notes, t_unit=0.02):
    """Translate the intermediate data into final output MIDI file."""

    midi = pretty_midi.PrettyMIDI()
    piano = pretty_midi.Instrument(program=0)

    # Some tricky steps to determine the velocity of the notes
    l, u = find_min_max_stren(notes)
    s_low = 60
    s_up = 127
    v_map = lambda stren: int(s_low + ((s_up - s_low) * ((stren - l) / (u - l + 0.0001))))

    low_b = note_to_midi("A0")
    coll = set()
    for nn in notes:
        pitch = nn["pitch"] + low_b
        start = nn["start"] * t_unit
        end = nn["end"] * t_unit
        volume = v_map(nn["stren"])
        coll.add(pitch)
        m_note = pretty_midi.Note(velocity=volume, pitch=pitch, start=start, end=end)
        piano.notes.append(m_note)
    midi.instruments.append(piano)
    return midi


def interpolation(data, ori_t_unit=0.02, tar_t_unit=0.01):
    """Interpolate between each frame to increase the time resolution.

    The default setting of feature extraction has time resolution of 0.02 seconds for each frame.
    To fit the conventional evaluation settings, which has time resolution of 0.01 seconds, we additionally
    apply the interpolation function to increase time resolution. Here we use `Cubic Spline` for the
    estimation.
    """
    assert len(data.shape) == 2

    ori_x = np.arange(len(data))
    tar_x = np.arange(0, len(data), tar_t_unit / ori_t_unit)
    func = CubicSpline(ori_x, data, axis=0)
    return func(tar_x)


def norm(data):
    return (data - np.mean(data)) / np.std(data)


def norm_onset_dura(pred, onset_th, dura_th, interpolate=True, normalize=True):
    """Normalizes prediction values of onset and duration channel."""

    length = len(pred) * 2 if interpolate else len(pred)
    norm_pred = np.zeros((length,) + pred.shape[1:])
    onset = interpolation(pred[:, :, 2])
    dura = interpolation(pred[:, :, 1])

    onset = np.where(onset < dura, 0, onset)
    norm_onset = norm(onset) if normalize else onset
    onset = np.where(norm_onset < onset_th, 0, norm_onset)
    norm_pred[:, :, 2] = onset

    norm_dura = norm(dura) + onset if normalize else dura + onset
    dura = np.where(norm_dura < dura_th, 0, norm_dura)
    norm_pred[:, :, 1] = dura

    return norm_pred


def norm_split_onset_dura(pred, onset_th, lower_onset_th, split_bound, dura_th, interpolate=True, normalize=True):
    """An advanced version of function for normalizing onset and duration channel.

    From the extensive experiments, we observe that the average prediction value for high and low frequency are different.
    Lower pitches tend to have smaller values, while higher pitches having larger. To acheive better transcription
    results, the most straight-forward solution is to assign different thresholds for low and high frequency part.
    And this is what this function provides for the purpose.

    Parameters
    ----------
    pred
        The predictions.
    onset_th : float
        Threshold for high frequency part.
    lower_onset_th : float
        Threshold for low frequency part.
    split_bound : int
        The split point of low and high frequency part. Value should be within 0~87.
    interpolate : bool
        Whether to apply interpolation between each frame to increase time resolution.
    normalize : bool
        Whether to normalize the prediction values.
    
    Returns
    -------
    pred
        Thresholded prediction, having value either 0 or 1.
    """

    upper_range = range(4 * split_bound, 352)
    upper_pred = pred[:, upper_range]
    upper_pred = norm_onset_dura(upper_pred, onset_th, dura_th, interpolate=interpolate, normalize=normalize)

    lower_range = range(4 * split_bound)
    lower_pred = pred[:, lower_range]
    lower_pred = norm_onset_dura(lower_pred, lower_onset_th, dura_th, interpolate=interpolate, normalize=normalize)

    return np.hstack([lower_pred, upper_pred])


def threshold_type_converter(th, length):
    """Convert scalar value to a list with the same value."""
    if isinstance(th, list):
        assert len(th) == length
    else:
        th = [th for _ in range(length)]
    return th


def entropy(data, bins=200):
    min_v = -20
    max_v = 30
    interval = (max_v - min_v) / bins
    cut_offs = [min_v + i * interval for i in range(bins + 1)]
    discrete_v = np.digitize(data, cut_offs)
    _, counts = np.unique(discrete_v, return_counts=True)
    probs = counts / np.sum(counts)
    ent = 0
    for p in probs:
        ent -= p * math.log(p, math.e)

    return ent


def note_inference(
    pred,
    mode="note",
    onset_th=7.5,
    lower_onset_th=None,
    split_bound=36,
    dura_th=2,
    frm_th=1,
    normalize=True,
    t_unit=0.02,
):
    if mode.startswith("note"):
        if lower_onset_th is not None:
            norm_pred = norm_split_onset_dura(
                pred,
                onset_th=onset_th,
                lower_onset_th=lower_onset_th,
                split_bound=split_bound,
                dura_th=dura_th,
                interpolate=True,
                normalize=normalize,
            )
        else:
            norm_pred = norm_onset_dura(pred, onset_th=onset_th, dura_th=dura_th, interpolate=True, normalize=normalize)

        norm_pred = np.where(norm_pred > 0, norm_pred + 1, 0)
        notes = infer_piece(down_sample(norm_pred), t_unit=0.01)
        midi = to_midi(notes, t_unit=t_unit / 2)

    elif mode.startswith("frame") or mode == "true_frame":
        ch_num = pred.shape[2]
        if ch_num == 2:
            mix = pred[:, :, 1]
        elif ch_num == 3:
            mix = (pred[:, :, 1] + pred[:, :, 2]) / 2
        else:
            raise ValueError("Unknown channel length: {}".format(ch_num))

        prob = norm(mix) if normalize else mix
        prob = np.where(prob > frm_th, 1, 0)
        prob = roll_down_sample(prob)

        notes = []
        for idx in range(prob.shape[1]):
            p_note = find_occur(prob[:, idx], t_unit=t_unit)
            for nn in p_note:
                note = {
                    "pitch": idx,
                    "start": nn["onset"],
                    "end": nn["offset"],
                    "stren": mix[int(nn["onset"] * t_unit), idx * 4],
                }
                notes.append(note)
        midi = to_midi(notes, t_unit=t_unit)

    else:
        raise ValueError(f"Supported mode are ['note', 'frame']. Given mode: {mode}")

    return midi


def multi_inst_note_inference(
    pred,
    mode="note-stream",
    onset_th=5,
    dura_th=2,
    frm_th=1,
    inst_th=0.95,
    normalize=True,
    t_unit=0.02,
    channel_program_mapping=MUSICNET_INSTRUMENT_PROGRAMS,
):
    """Function for infering raw multi-instrument predictions.

    Parameters
    ----------
    mode : {'note-stream', 'note', 'frame-stream', 'frame'}
        Inference mode. 
        Difference between 'note' and 'frame' is that the former consists of two note attributes, which are 'onset' and
        'duration', and the later only contains 'duration', which in most of the cases leads to worse listening 
        experience.
        With postfix 'stream' refers to transcribe instrument at the same time, meaning classifying each notes into 
        instrument classes, or says different tracks.
    onset_th : float
        Threshold of onset channel. Type of list or float
    dura_th : float
        Threshold of duration channel. Type of list or float
    inst_th : float
        Threshold of deciding a instrument is present or not according to Std. of prediction.
    normalize : bool
        Whether to normalize the predictions. For more details, please refer to our 
        `paper <https://bit.ly/2QhdWX5>`_
    t_unit : float
        Time unit for each frame. Should not be modified unless you have different settings during the feature 
        extraction
    channel_program_mapping : list[int]
        Mapping prediction channels to MIDI program numbers.
    
    Returns
    -------
    out_midi
        A pretty_midi.PrettyMIDI object.
    
    References
    ----------
    Publications can be found `here <https://bit.ly/2QhdWX5>`_.
    """

    if mode == "note-stream" or mode == "note":
        ch_per_inst = 2
    elif mode == "frame-stream" or mode == "frame":
        ch_per_inst = 2
    elif mode == "true_frame":
        # For older version model compatibility that was trained on pure frame-level.
        mode = "frame"
        ch_per_inst = 1
    else:
        raise ValueError
    assert (pred.shape[-1] - 1) % ch_per_inst == 0, f"Input shape: {pred.shape}"

    ch_container = []
    iters = (pred.shape[-1] - 1) // ch_per_inst
    for i in range(ch_per_inst):
        # First item would be duration channel
        # Second item would be onset channel
        item = pred[:, :, [it * ch_per_inst + i + 1 for it in range(iters)]]
        ch_container.append(norm(item) if normalize else item)

    if not mode.endswith("-stream") and mode != "true_frame":
        # Some different process for none-instrument care cases
        # Merge all channels into first channel
        iters = 1
        for i in range(ch_per_inst):
            pp = ch_container[i]
            pp[:, :, 0] = np.average(pp, axis=2)
            ch_container[i] = pp

    onset_th = threshold_type_converter(onset_th, iters)
    dura_th = threshold_type_converter(dura_th, iters)
    frm_th = threshold_type_converter(frm_th, iters)

    zeros = np.zeros((pred.shape[:-1]))
    out_midi = pretty_midi.PrettyMIDI()
    for i in range(iters):
        normed_ch = []
        std = 0
        ent = 0
        for ii in range(ch_per_inst):
            ch = ch_container[ii][:, :, i]
            std += np.std(ch)
            ent += entropy(ch)
            normed_ch.append(ch)
        print(
            "std: {:.3f} ent: {:.3f} mult: {:.3f}".format(
                std / ch_per_inst, ent / ch_per_inst, std * ent / ch_per_inst ** 2
            )
        )
        if iters > 1 and (std / ch_per_inst < inst_th):
            continue

        pp = np.dstack([zeros] + normed_ch)
        midi = note_inference(
            pp,
            mode=mode,
            onset_th=onset_th[i],
            dura_th=dura_th[i],
            frm_th=frm_th[i],
            normalize=normalize,
            t_unit=t_unit,
        )

        inst_program = channel_program_mapping[i]
        inst_name = MIDI_PROGRAM_NAME_MAPPING[str(inst_program)]
        inst = pretty_midi.Instrument(program=inst_program, name=inst_name)
        inst.notes = midi.instruments[0].notes
        out_midi.instruments.append(inst)

    return out_midi