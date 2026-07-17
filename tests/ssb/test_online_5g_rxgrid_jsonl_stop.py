from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import sys
import types
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "python"
    / "ssb_python"
    / "online_5g_rxgrid_jsonl.py"
)


def load_receiver(monkeypatch):
    numpy = types.ModuleType("numpy")
    monkeypatch.setitem(sys.modules, "numpy", numpy)

    cfo_utils = types.ModuleType("cfo_utils")
    cfo_utils.apply_frequency_correction = lambda **kwargs: kwargs["waveform"]
    monkeypatch.setitem(sys.modules, "cfo_utils", cfo_utils)

    cfo_warmup = types.ModuleType("capture_online_rxgridssb_dataset_cfo")
    cfo_warmup.estimate_cfo_warmup = lambda **_kwargs: (0.0, [])
    monkeypatch.setitem(
        sys.modules,
        "capture_online_rxgridssb_dataset_cfo",
        cfo_warmup,
    )

    pipeline = types.ModuleType("profile_online_datassb_pipeline")
    pipeline.capture_one_block = lambda **_kwargs: object()
    pipeline.configure_usrp = lambda _args: None
    pipeline.extract_rxgrid_from_waveform = lambda **_kwargs: None
    pipeline.make_rx_streamer = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "profile_online_datassb_pipeline", pipeline)

    spec = importlib.util.spec_from_file_location("online_5g_rxgrid_jsonl_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sigint_finishes_current_iteration_before_final_statistics(
    tmp_path,
    monkeypatch,
    capsys,
):
    receiver = load_receiver(monkeypatch)
    output = tmp_path / "rxgridssb.jsonl"

    class FakeUsrp:
        def get_rx_rate(self, _channel):
            return 15.36e6

        def get_rx_freq(self, _channel):
            return 3541.44e6

        def get_rx_gain(self, _channel):
            return 60.0

    class FakeStreamer:
        def get_max_num_samps(self):
            return 1024

    class FakeGrid:
        shape = (240, 4)

    args = argparse.Namespace(
        output_jsonl=str(output),
        truncate_output=True,
        serial="test",
        freq=3541.44e6,
        rate=15.36e6,
        gain=60.0,
        duration_ms=20.0,
        channel=0,
        antenna="",
        settle_sec=0.0,
        nfft=512,
        demod_rb=30,
        nrb_ssb=20,
        num_symbols=6,
        force_nid2=0,
        min_pss_metric=0.0,
        enable_cfo_correction=False,
        manual_cfo_hz=None,
        cfo_correction_sign=-1.0,
        cfo_warmup_iters=0,
        num_iters=0,
        write_invalid=False,
        progress_every=0,
    )
    monkeypatch.setattr(receiver, "parse_args", lambda: args)
    monkeypatch.setattr(receiver, "configure_usrp", lambda _args: FakeUsrp())
    monkeypatch.setattr(
        receiver,
        "make_rx_streamer",
        lambda *_args, **_kwargs: FakeStreamer(),
    )

    def capture_and_request_stop(**_kwargs):
        os.kill(os.getpid(), signal.SIGINT)
        return object()

    monkeypatch.setattr(receiver, "capture_one_block", capture_and_request_stop)
    monkeypatch.setattr(
        receiver,
        "extract_rxgrid_from_waveform",
        lambda **_kwargs: (
            None,
            FakeGrid(),
            {"metric": 1.0, "n_symbols_extracted": 6},
            {},
        ),
    )
    monkeypatch.setattr(
        receiver,
        "make_payload",
        lambda **kwargs: {"iteration": kwargs["iteration"]},
    )

    previous_handler = signal.getsignal(signal.SIGINT)
    receiver.main()

    assert signal.getsignal(signal.SIGINT) is previous_handler
    assert [json.loads(line) for line in output.read_text().splitlines()] == [
        {"iteration": 0}
    ]
    stdout = capsys.readouterr().out
    assert "Stopping after Ctrl+C." in stdout
    assert "iterations:         1" in stdout
    assert "valid grids:        1" in stdout
    assert "invalid grids:      0" in stdout
    assert "JSONL lines written:1" in stdout
