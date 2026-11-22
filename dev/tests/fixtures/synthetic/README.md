# Synthetic fixtures

`synthetic/<SID>/` holds hand-made fixtures for the slice `<SID>`, used when a live run is not
possible or the user declined consent.

- Every file here starts with a `synthetic: true` header (a YAML key, or a comment line in
  formats without one), so no reader can mistake it for a recorded capture.
- An item backed only by synthetic fixtures stays UNVERIFIED. A synthetic fixture never turns a
  verdict into a pass; only a capture under `../recorded/<SID>/` can.
- Novel outputs an audit produces (`novel_fixture_needed`) go to `tests/candidate_outputs/<SID>/`,
  never here. They stay there until the user promotes them into `dev/tests/fixtures/`.
