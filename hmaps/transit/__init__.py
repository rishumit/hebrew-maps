"""Offline public-transport journey planning (GTFS → compact numpy store → RAPTOR).

    hmaps.transit.gtfs     ETL: GTFS zip → data/transit/transit-<ts>.npz (+ transit.current pointer)
    hmaps.transit.data     loading the store, service days, names
    hmaps.transit.raptor   round-based public transit routing (vectorised with numpy)
    hmaps.transit.planner  queries: itineraries with Hebrew text, stop departures, status

See docs/adr/0007-transit.md.
"""
