// Sensible pre-filled defaults so a user can hit Search immediately.
export function isoDaysFromNow(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export function defaultSearch() {
  return {
    movie_title: "",
    format: "Any",
    formats: ["Any"],
    location: "94103",
    radius_miles: 25,
    date_from: isoDaysFromNow(0),
    date_to: isoDaysFromNow(14),
    time_rule: {
      weekday_cutoff: "18:30",
      weekends_unrestricted: true,
    },
    seats_together: 4,
    min_row: 5,
  };
}
