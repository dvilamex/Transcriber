from transcriber.chunking import chunk_segments
from transcriber.models import Segment


def _segments(words, words_per_segment=20):
    segments = []
    start = 0.0
    for index in range(0, len(words), words_per_segment):
        text = " ".join(words[index : index + words_per_segment])
        segments.append(Segment(start=start, duration=8.0, text=text))
        start += 8.0
    return segments


def test_long_podcast_is_split_with_overlap_and_full_coverage():
    words = [f"w{index}" for index in range(35000)]
    chunks = chunk_segments(_segments(words), max_chars=12000, overlap_chars=500)
    assert len(chunks) > 10
    assert "w0" in chunks[0].text
    assert "w34999" in chunks[-1].text
    for earlier, later in zip(chunks, chunks[1:]):
        assert len(earlier.text) <= 12000 or earlier.text.count("\n") == 0
        earlier_lines = earlier.text.splitlines()
        later_lines = set(later.text.splitlines())
        assert earlier_lines[-1] in later_lines
        assert chunks.index(later) == earlier.index + 1


def test_chunker_advances_past_a_single_oversized_line():
    huge = Segment(start=0, duration=1, text="word " * 5000)
    following = Segment(start=2, duration=1, text="after the huge line")
    chunks = chunk_segments([huge, following], max_chars=1000, overlap_chars=100)
    assert len(chunks) == 2
    assert "after the huge line" in chunks[1].text
    assert chunks[0].text != chunks[1].text
