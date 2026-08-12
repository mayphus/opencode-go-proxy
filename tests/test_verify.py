import json

from opencode_go_proxy.verify import (
    CheckResult,
    has_url_citation,
    output_items,
    output_text,
    red_png,
    verification_report,
)


def test_generated_red_png_is_true_color_32_by_32():
    image = red_png()

    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert image[16:24] == b"\x00\x00\x00 \x00\x00\x00 "
    assert image[24:26] == b"\x08\x02"


def test_output_text_falls_back_to_message_content():
    response = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}]}

    assert output_text(response) == "hello"


def test_output_items_selects_requested_native_type():
    response = {"output": [{"type": "web_search_call"}, {"type": "message"}]}

    assert output_items(response, "web_search_call") == [{"type": "web_search_call"}]


def test_url_citation_detection():
    response = json.loads('''{
      "output": [{"type":"message","content":[{"type":"output_text","text":"Python",
        "annotations":[{"type":"url_citation","url":"https://www.python.org/"}]}]}]
    }''')

    assert has_url_citation(response)


def test_verification_report_distinguishes_evidence_states():
    report = verification_report("go/gpt-5.6-luna", [
        CheckResult("web_search", True, "citation observed"),
        CheckResult("prompt_cache_options", False, "HTTP 400"),
    ])

    assert report["model"] == "go/gpt-5.6-luna"
    assert report["capabilities"]["web_search"]["status"] == "verified"
    assert report["capabilities"]["prompt_caching"]["status"] == "rejected"
    assert report["capabilities"]["file_search"]["status"] == "untested"
