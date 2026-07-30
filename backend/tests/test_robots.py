"""robots.txt parsing, using the exact real-world files that broke the stdlib one.

Both fixtures below are verbatim shapes captured from live hosts on 2026-07-29.
"""
import pytest

from app import robots

# cinemark.com writes ONE directive per group -- nine separate `User-agent: *`
# blocks, with a UTF-8 BOM and CRLF line endings. urllib.robotparser keeps only
# the first group, so it missed `Disallow: /TicketSeatMap` entirely.
CINEMARK = (
    "﻿User-agent: *  \r\nDisallow: /employee-forms/  \r\n\r\n"
    "User-agent: *  \r\nDisallow: /media/  \r\n\r\n"
    "User-agent: *  \r\nDisallow: /tickets/ \r\n\r\n"
    "User-agent: *\r\nDisallow: /ticketseatmap/\r\n\r\n"
    "User-agent: *\r\nDisallow: /TicketSeatMap\r\n\r\n"
    "User-agent: *\r\nDisallow: /shoppingcart\r\n\r\n"
    "Sitemap: https://www.cinemark.com/sitemap.xml\r\n"
    "User-agent: *\r\nCrawl-delay: 10\r\n"
)

AMC = """User-Agent: Googlebot-News
Allow: /amc-scene/
Disallow: /

User-Agent: meta-externalagent
Disallow: /showtimes/null

User-Agent: *
Disallow: /amc-stubs-wifi/
Disallow: /associate-resources
Disallow: /search?*

Sitemap: https://www.amctheatres.com/sitemap.xml
"""

UA = "showtime-finder"


def test_all_wildcard_groups_are_merged():
    """The bug that mattered: a later `User-agent: *` block must still apply."""
    rf = robots.parse(CINEMARK)
    assert not rf.can_fetch(UA, "https://www.cinemark.com/TicketSeatMap/?TheaterId=477")
    assert not rf.can_fetch(UA, "https://www.cinemark.com/ticketseatmap/")
    assert not rf.can_fetch(UA, "https://www.cinemark.com/shoppingcart")
    # The first group still applies too.
    assert not rf.can_fetch(UA, "https://www.cinemark.com/employee-forms/x")


def test_cinemark_allows_theatre_listing_pages():
    rf = robots.parse(CINEMARK)
    assert rf.can_fetch(
        UA, "https://www.cinemark.com/theatres/ca-san-jose/century-20-oakridge-and-xd?showDate=2026-08-01"
    )


def test_bom_does_not_swallow_the_first_directive():
    rf = robots.parse(CINEMARK)
    assert rf.groups
    assert not rf.can_fetch(UA, "https://www.cinemark.com/employee-forms/")


def test_amc_allows_seat_pages_but_not_search():
    rf = robots.parse(AMC)
    assert rf.can_fetch(UA, "https://www.amctheatres.com/showtimes/144251397/seats")
    assert rf.can_fetch(
        UA, "https://www.amctheatres.com/movie-theatres/san-francisco/amc-metreon-16/showtimes?date=2026-08-01"
    )
    # Wildcard pattern with a query string.
    assert not rf.can_fetch(UA, "https://www.amctheatres.com/search?q=odyssey")
    assert not rf.can_fetch(UA, "https://www.amctheatres.com/associate-resources")


def test_rules_for_other_agents_do_not_apply_to_us():
    """Googlebot-News is disallowed everything; that must not bind us."""
    rf = robots.parse(AMC)
    assert rf.can_fetch(UA, "https://www.amctheatres.com/anything")


def test_html_body_is_rejected_rather_than_read_as_permissive():
    """amctheatres.com serves an HTML bot-check page for /robots.txt.

    Parsing that as robots yields zero rules, which reads as "allow everything" —
    the unsafe direction. It must raise so callers can fail closed.
    """
    html = '<!DOCTYPE html>\n<html><head><meta name="robots" content="noindex">'
    with pytest.raises(robots.NotRobotsTxt):
        robots.parse(html)


def test_longest_match_wins_and_allow_breaks_ties():
    rf = robots.parse(
        "User-agent: *\nDisallow: /movies/\nAllow: /movies/the-odyssey\n"
    )
    assert not rf.can_fetch(UA, "https://x.test/movies/other")
    assert rf.can_fetch(UA, "https://x.test/movies/the-odyssey")


def test_dollar_anchors_end_of_path():
    rf = robots.parse("User-agent: *\nDisallow: /seats$\n")
    assert not rf.can_fetch(UA, "https://x.test/seats")
    assert rf.can_fetch(UA, "https://x.test/seats/1")


def test_empty_disallow_means_allow_all():
    rf = robots.parse("User-agent: *\nDisallow:\n")
    assert rf.can_fetch(UA, "https://x.test/anything")


def test_consecutive_user_agent_lines_share_one_group():
    rf = robots.parse("User-agent: a\nUser-agent: b\nDisallow: /x\n")
    assert len(rf.groups) == 1
    assert rf.groups[0].agents == {"a", "b"}


def test_no_directives_marks_file_empty():
    rf = robots.parse("# just a comment\n\n")
    assert rf.empty
    assert rf.can_fetch(UA, "https://x.test/anything")
