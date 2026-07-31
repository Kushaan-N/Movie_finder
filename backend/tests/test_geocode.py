"""Geocoding the location box.

An unplaceable location does not raise — it just stops bounding anything: the
radius filter is skipped and every theater gets queried. That makes a geocode
miss expensive and invisible, which is why the forgiving matching below is
tested rather than assumed.
"""
from app.services.theaters import candidate_theaters, geocode


class TestGeocodeAcceptsHowPeopleType:
    def test_bare_city(self):
        assert geocode("san francisco") is not None

    def test_city_with_state_abbreviation(self):
        # The regression: matching was exact-key plus single-token, so every
        # multi-word city became unreachable the moment ", CA" was appended --
        # and ", CA" is what a person actually types.
        assert geocode("San Francisco, CA") == geocode("san francisco")

    def test_city_with_state_spelled_out(self):
        assert geocode("San Jose, California") == geocode("san jose")

    def test_case_and_punctuation_are_irrelevant(self):
        assert geocode("  SAN   JOSE ,  ca.  ") == geocode("san jose")

    def test_zip_anywhere_in_the_string(self):
        assert geocode("94105, USA") == geocode("94105")

    def test_zip_beats_the_city_it_sits_in(self):
        # A ZIP names a neighbourhood; the city centre is a coarser answer.
        assert geocode("San Francisco, CA 94105") == geocode("94105")
        assert geocode("94105") != geocode("san francisco")

    def test_qualifier_before_the_city(self):
        assert geocode("Downtown San Jose, CA") == geocode("san jose")

    def test_unknown_place_is_none_not_a_guess(self):
        assert geocode("Timbuktu") is None

    def test_blank_is_none(self):
        assert geocode("") is None
        assert geocode("   ") is None

    def test_a_bare_fragment_does_not_match_a_city(self):
        # "san" must not resolve to San Francisco or San Jose -- whole words only.
        assert geocode("san") is None


class TestTheatersJsonWidensTheTable:
    def test_a_theaters_city_geocodes_without_a_hand_written_entry(self):
        # Every theater carries an address and coordinates, so the places we
        # serve geocode themselves.
        from app.services.theaters import load_theaters

        cities = {t.address.split(",")[-2].strip() for t in load_theaters() if t.address}
        assert cities, "fixture theaters should have addresses"
        for city in cities:
            assert geocode(city) is not None, f"{city} should geocode from theaters.json"


class TestRadiusActuallyBounds:
    def test_radius_excludes_far_theaters(self):
        near = candidate_theaters("San Francisco, CA", 5, [])
        far = candidate_theaters("San Francisco, CA", 60, [])
        assert len(near) < len(far)
        assert all(d is not None and d <= 5 for _, d in near)

    def test_every_candidate_has_a_distance_when_placed(self):
        for _, dist in candidate_theaters("San Jose, CA", 25, []):
            assert dist is not None

    def test_unplaceable_location_returns_everything_undistanced(self):
        # Documented behaviour, not an accident: we would rather show unbounded
        # results than none. run_search says so in a note.
        got = candidate_theaters("Timbuktu", 1, [])
        assert got and all(d is None for _, d in got)

    def test_results_are_nearest_first(self):
        dists = [d for _, d in candidate_theaters("San Jose, CA", 60, [])]
        assert dists == sorted(dists)
