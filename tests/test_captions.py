from transcriber.language import accept_transcript, looks_english
from transcriber.transcript import dedupe_rolling, parse_vtt
from transcriber.models import Segment


ENGLISH = (
    "The founder said that the market is not ready for agents and that inference "
    "costs are falling faster than people think. We have seen this in the last "
    "year and it will matter for every software company that sells a seat."
)
SPANISH = (
    "El fundador dijo que el mercado no está listo para los agentes porque los "
    "costos de inferencia están bajando y las empresas no pueden adoptar esta "
    "tecnología todavía en producción con sus equipos actuales de datos."
)


def test_english_check_rejects_dubbed_text():
    assert looks_english(ENGLISH)
    assert not looks_english(SPANISH)
    assert accept_transcript(ENGLISH, "en")
    assert not accept_transcript(SPANISH, "es")
    assert not accept_transcript(SPANISH, "en")
    assert accept_transcript("Hello there.", "en")
    assert not accept_transcript("Hola a todos.", "es")
    dubbed = (
        "El mercado no esta listo para agentes porque los costos siguen altos "
        "y las empresas no pueden cambiar su plataforma este trimestre."
    )
    assert not accept_transcript(dubbed, "en")


def test_rolling_captions_collapse_to_one_line():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:02.000
Inference prices

00:00:01.500 --> 00:00:03.000
Inference prices are falling

00:00:04.000 --> 00:00:06.000
The guest disagreed
"""
    segments = parse_vtt(vtt)
    assert [segment.text for segment in segments] == [
        "Inference prices are falling",
        "The guest disagreed",
    ]
    assert dedupe_rolling(
        [
            Segment(0, 1, "Hello"),
            Segment(1, 1, "Hello world"),
        ]
    )[0].text == "Hello world"
