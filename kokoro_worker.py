"""Run Kokoro in an isolated process and synthesize complete reply text."""
import base64
import json
import os
import sys
import traceback
from contextlib import redirect_stdout


def main():
    if len(sys.argv) != 2 or len(sys.argv[1]) < 2:
        raise ValueError("a Kokoro voice ID is required")

    voice = sys.argv[1]
    pipeline = None
    np = None

    def load_pipeline():
        nonlocal pipeline, np
        if pipeline is None:
            os.environ.setdefault("OMP_NUM_THREADS", "2")
            os.environ.setdefault("MKL_NUM_THREADS", "2")
            import numpy
            import torch
            from kokoro import KPipeline

            torch.set_num_threads(2)
            torch.set_num_interop_threads(1)
            np = numpy
            pipeline = KPipeline(lang_code=voice[0])

    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("warmup"):
                with redirect_stdout(sys.stderr):
                    load_pipeline()
                result = {"ready": True}
                sys.stdout.write(json.dumps(result) + "\n")
                sys.stdout.flush()
                continue
            text = str(request.get("text", "")).strip()
            if not text:
                raise ValueError("Kokoro received an empty text chunk.")
            with redirect_stdout(sys.stderr):
                load_pipeline()
                audio_chunks = []
                word_timings = []
                elapsed_samples = 0
                for result in pipeline(text, voice=voice, speed=1.0):
                    audio = result.audio
                    if hasattr(audio, "detach"):
                        audio = audio.detach().cpu().numpy()
                    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
                    if samples.size:
                        audio_chunks.append(samples)
                        sample_offset = elapsed_samples / 24000.0
                        for token in getattr(result, "tokens", None) or ():
                            end_time = getattr(token, "end_ts", None)
                            token_text = getattr(token, "text", "")
                            if token_text and end_time is not None:
                                word_timings.append({
                                    "text": token_text,
                                    "end_time_seconds": sample_offset + float(end_time),
                                })
                        elapsed_samples += samples.size
                if not audio_chunks:
                    raise ValueError("Kokoro returned no audio for this text chunk.")
                samples = np.concatenate(audio_chunks)
                pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                result = {
                    "audio_base64": base64.b64encode(pcm).decode("ascii"),
                    "sample_count": int(samples.size),
                    "word_timings": word_timings,
                }
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            result = {"error": str(exc)[:240] or "Kokoro synthesis failed."}
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
