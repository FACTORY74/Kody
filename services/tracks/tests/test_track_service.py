# Kody — General-Purpose Mapping Application
#
# A Python-based mapping application with a JavaScript/HTML web interface and a plugin architecture for extending functionality.
#
# Copyright (c) 2026 Factory74
#
# SPDX-License-Identifier: MIT
#
# Distributed under the MIT License. See LICENSE for details.
# Provided "AS IS", without warranty of any kind.

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from kody_tracks import track_service
from kody_tracks.track_service import (
    Aircraft,
    AircraftLimits,
    AircraftType,
    AircraftTypeCatalogue,
    Route,
    RouteLeg,
    ScenarioCatalogue,
    TrackStore,
)


def aircraft_types() -> AircraftTypeCatalogue:
    return AircraftTypeCatalogue(types=[AircraftType(
        id="test-plane",
        displayName="Test plane",
        symbol="plane",
        limits=AircraftLimits(
            minimumSpeedMetresPerSecond=10,
            maximumSpeedMetresPerSecond=100,
            minimumAltitudeMetres=0,
            maximumAltitudeMetres=10_000,
            maximumClimbRateMetresPerSecond=10,
            maximumDescentRateMetresPerSecond=10,
            maximumTurnRateDegreesPerSecond=5,
            maximumBankAngleDegrees=30,
            maximumAccelerationMetresPerSecondSquared=10,
            maximumDecelerationMetresPerSecondSquared=10,
        ),
    )])


def scenario(policy: str = "hold") -> ScenarioCatalogue:
    return ScenarioCatalogue(
        routes=[Route(
            id="route-1", name="Test route", color="#0099ff", completionPolicy=policy,
            legs=[
                RouteLeg(id="one", waypoint=[0, 0], targetAltitudeMetres=100, targetSpeedMetresPerSecond=20),
                RouteLeg(id="two", waypoint=[0.01, 0], targetAltitudeMetres=200, targetSpeedMetresPerSecond=30),
            ],
        )],
        aircraft=[Aircraft(
            id="aircraft-1", name="Test aircraft", aircraftTypeId="test-plane", routeId="route-1",
            color="#ff9900", initialPosition=[-0.0005, 0], initialAltitudeMetres=0,
            initialSpeedMetresPerSecond=10,
        )],
    )


class TrackStoreTests(unittest.TestCase):
    @staticmethod
    def configure_test_catalogues(store: TrackStore) -> None:
        store.update_scenario(ScenarioCatalogue(), None)
        store.update_aircraft_types(aircraft_types(), None)

    def test_first_authoritative_tick_replays_from_simulation_origin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: 1_000.0)

            store._tick(25.0)

            self.assertEqual(store.last_tick, 25.0)
            self.assertEqual(store.simulation_seconds, 25.0)

    def test_discards_legacy_checkpoint_and_replays_from_simulation_origin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: 0.0)
            self.configure_test_catalogues(original)
            original.update_scenario(scenario(), None, now=0)
            original.response(now=5)

            checkpoint_path = root / "runtime.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["schemaVersion"] = 1
            checkpoint.pop("scenarioStartSeconds")
            checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

            restarted = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: 20.0)
            restarted.response(now=20)

            self.assertEqual(restarted.simulation_seconds, 20.0)
            self.assertNotEqual(restarted.runtime["aircraft-1"].position, [0, 0])

    def test_restores_runtime_checkpoint_after_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = [100.0]
            original = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: clock[0])
            self.configure_test_catalogues(original)
            original.update_scenario(scenario(), None, now=clock[0])
            clock[0] += 3
            moved = original.response()
            moved_position = next(
                item for item in moved["features"] if item["id"] == "track:aircraft-1"
            )["geometry"]["coordinates"]

            restarted = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: clock[0])
            clock[0] += 1
            resumed = restarted.response()
            resumed_position = next(
                item for item in resumed["features"] if item["id"] == "track:aircraft-1"
            )["geometry"]["coordinates"]

            self.assertNotEqual(moved_position, [0, 0])
            self.assertNotEqual(resumed_position, [0, 0])
            self.assertGreater(restarted.simulation_seconds, original.simulation_seconds)

    def test_persists_catalogues_and_renders_route_waypoints_aircraft_and_trail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = [1_000.0]
            store = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: clock[0])
            self.configure_test_catalogues(store)
            store.update_scenario(scenario(), None)

            response = store.response()
            features = {feature["id"]: feature for feature in response["features"]}

            self.assertTrue((root / "types.json").is_file())
            self.assertTrue((root / "scenario.json").is_file())
            self.assertEqual(features["route:route-1"]["geometry"]["type"], "LineString")
            self.assertEqual(features["waypoint:route-1:one"]["properties"]["kind"], "route-waypoint")
            self.assertEqual(features["track:aircraft-1"]["properties"]["kody"]["selectionLabel"], "Test aircraft")
            self.assertEqual(features["track:aircraft-1"]["geometry"]["coordinates"], [0, 0])
            self.assertEqual(features["track:aircraft-1"]["properties"]["activeLegId"], "two")
            self.assertEqual(features["track:aircraft-1"]["properties"]["altitudeBarometricFeet"], 0)
            self.assertEqual(features["trail:aircraft-1"]["geometry"]["type"], "LineString")

    def test_transitions_speed_altitude_and_advances_legs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = [1_000.0]
            root = Path(directory)
            store = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: clock[0])
            self.configure_test_catalogues(store)
            store.update_scenario(scenario(), None)
            clock[0] += 2

            response = store.response()
            aircraft = next(feature for feature in response["features"] if feature["id"] == "track:aircraft-1")

            self.assertEqual(aircraft["properties"]["activeLegId"], "two")
            self.assertEqual(aircraft["properties"]["speedMetresPerSecond"], 30)
            self.assertEqual(aircraft["properties"]["altitudeBarometricFeet"], 20 * 3.28084)

    def test_rejects_invalid_references_limits_and_stale_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TrackStore(root / "types.json", root / "scenario.json")
            self.configure_test_catalogues(store)
            with self.assertRaises(HTTPException) as stale:
                store.update_aircraft_types(aircraft_types(), "stale")
            self.assertEqual(stale.exception.status_code, 409)

            invalid = scenario()
            invalid.aircraft[0].routeId = "missing"
            with self.assertRaises(HTTPException) as reference:
                store.update_scenario(invalid, None)
            self.assertEqual(reference.exception.status_code, 422)

            aspirational = scenario()
            aspirational.routes[0].legs[0].targetSpeedMetresPerSecond = 1_000
            store.update_scenario(aspirational, None)

    def test_clamps_aspirational_target_speed_to_aircraft_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TrackStore(root / "types.json", root / "scenario.json")
            self.configure_test_catalogues(store)
            aspirational = scenario()
            aspirational.routes[0].legs[1].targetSpeedMetresPerSecond = 1_000
            store.update_scenario(aspirational, None)
            runtime = store.runtime["aircraft-1"]
            runtime.position = [-1, 0]
            runtime.legStartPosition = [-1, 0]

            store._advance_leg(runtime, aspirational.routes[0], aspirational.aircraft[0], 20)

            self.assertEqual(runtime.speedMetresPerSecond, 100)
            response = store.response()
            aircraft = next(feature for feature in response["features"] if feature["id"] == "track:aircraft-1")
            self.assertEqual(aircraft["properties"]["targetSpeedMetresPerSecond"], 1_000)
            self.assertEqual(aircraft["properties"]["effectiveTargetSpeedMetresPerSecond"], 100)

            aspirational.routes[0].legs[1].targetSpeedMetresPerSecond = 0
            store._advance_leg(runtime, aspirational.routes[0], aspirational.aircraft[0], 20)
            self.assertEqual(runtime.speedMetresPerSecond, 10)

            aspirational.routes[0].legs[1].targetSpeedMetresPerSecond = 55
            store._advance_leg(runtime, aspirational.routes[0], aspirational.aircraft[0], 20)
            self.assertEqual(runtime.speedMetresPerSecond, 55)

    def test_overflies_each_waypoint_before_rate_limited_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = [1_000.0]
            root = Path(directory)
            store = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: clock[0])
            self.configure_test_catalogues(store)
            turning_scenario = scenario("loop")
            turning_scenario.routes[0].legs[1].waypoint = [0, 0.01]
            store.update_scenario(turning_scenario, None)
            runtime = store.runtime["aircraft-1"]
            runtime.position = [-0.002, 0]
            runtime.legStartPosition = [-0.002, 0]
            runtime.headingDegrees = 0
            runtime.activeLegIndex = 0
            clock[0] += 1

            response = store.response()
            feature = next(item for item in response["features"] if item["id"] == "track:aircraft-1")

            self.assertEqual(feature["properties"]["activeLegId"], "one")
            self.assertEqual(feature["properties"]["bearingDegrees"], 5)

            runtime.position = [-0.0001, 0]
            runtime.headingDegrees = 90
            clock[0] += 1
            response = store.response()
            feature = next(item for item in response["features"] if item["id"] == "track:aircraft-1")
            self.assertEqual(feature["properties"]["activeLegId"], "two")
            self.assertNotEqual(feature["geometry"]["coordinates"], [0, 0])
            self.assertGreater(feature["properties"]["bearingDegrees"], 85)
            self.assertLess(feature["properties"]["bearingDegrees"], 90)

            clock[0] += 1
            response = store.response()
            feature = next(item for item in response["features"] if item["id"] == "track:aircraft-1")
            self.assertLess(feature["properties"]["bearingDegrees"], 85)
            self.assertGreaterEqual(feature["properties"]["legProgress"], 0)
            self.assertLessEqual(feature["properties"]["legProgress"], 1)
            self.assertIsNotNone(feature["properties"]["estimatedSecondsToWaypoint"])

    def test_pause_resume_and_reset_keep_simulation_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = [1_000.0]
            root = Path(directory)
            store = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: clock[0])
            self.configure_test_catalogues(store)
            store.update_scenario(scenario(), None)
            clock[0] += 2
            moved = store.response()
            position = next(item for item in moved["features"] if item["id"] == "track:aircraft-1")["geometry"]["coordinates"]

            revision = store.simulation_response()["revision"]
            store.update_simulation(track_service.SimulationControls(running=False), revision)
            clock[0] += 5
            paused = store.response()
            paused_position = next(item for item in paused["features"] if item["id"] == "track:aircraft-1")["geometry"]["coordinates"]
            self.assertEqual(paused_position, position)
            self.assertEqual(store.simulation_response()["elapsedSeconds"], 2)

            store.reset_simulation()
            reset = store.response()
            reset_position = next(item for item in reset["features"] if item["id"] == "track:aircraft-1")["geometry"]["coordinates"]
            self.assertEqual(reset_position, [0, 0])
            self.assertEqual(store.simulation_response()["elapsedSeconds"], 0)

    def test_clock_reset_rewinds_runtime_to_the_initial_scenario_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = [10.0]
            root = Path(directory)
            store = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: clock[0])
            self.configure_test_catalogues(store)
            store.update_scenario(scenario(), None)
            clock[0] += 2
            moved = store.response()
            moved_position = next(item for item in moved["features"] if item["id"] == "track:aircraft-1")["geometry"]["coordinates"]
            self.assertNotEqual(moved_position, [0, 0])

            clock[0] = 0
            reset = store.response()
            reset_position = next(item for item in reset["features"] if item["id"] == "track:aircraft-1")["geometry"]["coordinates"]
            self.assertEqual(reset_position, [0, 0])
            self.assertEqual(store.simulation_response()["elapsedSeconds"], 0)

    def test_ignores_out_of_order_clock_samples_at_the_same_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: 0.0)
            self.configure_test_catalogues(store)
            store.update_scenario(scenario(), None, now=0, clock_revision="1")
            current = store.response(now=10, clock_revision="1")
            current_position = next(
                item for item in current["features"] if item["id"] == "track:aircraft-1"
            )["geometry"]["coordinates"]

            stale = store.response(now=9.999, clock_revision="1")
            stale_position = next(
                item for item in stale["features"] if item["id"] == "track:aircraft-1"
            )["geometry"]["coordinates"]

            self.assertEqual(stale_position, current_position)
            self.assertEqual(store.simulation_seconds, 10)

            reset = store.response(now=0, clock_revision="2")
            reset_position = next(
                item for item in reset["features"] if item["id"] == "track:aircraft-1"
            )["geometry"]["coordinates"]
            self.assertEqual(reset_position, [0, 0])

    def test_scenario_update_uses_the_authoritative_simulation_clock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = [1_000.0]
            root = Path(directory)
            store = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: clock[0])
            self.configure_test_catalogues(store)

            store.update_scenario(scenario(), None, now=120)
            response = store.response(now=121)
            aircraft = next(feature for feature in response["features"] if feature["id"] == "track:aircraft-1")

            self.assertEqual(store.simulation_response()["elapsedSeconds"], 1)
            self.assertNotEqual(aircraft["geometry"]["coordinates"], [0, 0])

    def test_opening_workspace_does_not_reload_catalogues_or_reset_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = [1_000.0]
            store = TrackStore(root / "types.json", root / "scenario.json", clock=lambda: clock[0])
            self.configure_test_catalogues(store)
            store.update_scenario(scenario(), None, now=clock[0])
            reloaded_types = aircraft_types()
            reloaded_types.types[0].displayName = "Reloaded test plane"
            store.aircraft_types_path.write_text(
                reloaded_types.model_dump_json(indent=2),
                encoding="utf-8",
            )
            clock[0] += 2

            with (
                patch.object(track_service, "track_store", store),
                patch.object(track_service, "shared_simulation_clock", return_value=(clock[0], "1")),
            ):
                client = TestClient(track_service.app)
                workspace = client.get("/api/workspace")
                types     = client.get("/api/tracks/aircraft-types")

            self.assertEqual(workspace.status_code, 200)
            self.assertEqual(
                workspace.json()["panes"][1]["summary"]["items"][0]["title"],
                "Test plane",
            )
            self.assertEqual(types.status_code, 200)
            self.assertEqual(types.json()["types"][0]["displayName"], "Test plane")
            self.assertGreater(store.simulation_seconds, 0)

    def test_endpoints_expose_persisted_scenario_capability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TrackStore(root / "types.json", root / "scenario.json")
            with patch.object(track_service, "track_store", store):
                client = TestClient(track_service.app)
                manifest = client.get("/api/kody/manifest").json()
                capability = manifest["capabilities"]["scenarioEditor"]
                self.assertEqual(capability["revisionHeader"], "If-Match")
                self.assertEqual(capability["route"]["geometry"], "LineString")
                self.assertEqual(
                    capability["completionPolicies"],
                    ["despawn", "restart", "loop", "hold", "reverse"],
                )
                self.assertNotIn("simulationControls", manifest["capabilities"])
                self.assertEqual(
                    manifest["dependencies"]["simTimeControl"]["serviceId"],
                    "sim-time",
                )
                self.assertEqual(
                    manifest["capabilities"]["workspace"]["openPath"],
                    "/api/workspace",
                )
                workspace = client.get("/api/workspace")
                self.assertEqual(workspace.status_code, 200)
                self.assertEqual(workspace.json()["panes"][2]["id"], "routes")
                self.assertEqual(
                    workspace.json()["panes"][2]["summary"]["items"][0]["title"],
                    "South coast patrol",
                )
                self.assertEqual(
                    workspace.json()["panes"][4]["summary"]["metrics"][0]["value"],
                    "Running",
                )
                types_response = client.get("/api/tracks/aircraft-types")
                self.assertEqual(types_response.status_code, 200)
                scenario_response = client.get("/api/tracks/scenario")
                self.assertEqual(scenario_response.status_code, 200)
                scenario_payload = scenario_response.json()
                scenario_payload.pop("revision")
                update = client.put("/api/tracks/scenario", json=scenario_payload)
                self.assertEqual(update.status_code, 200)
                simulation = client.get("/api/tracks/simulation")
                self.assertTrue(simulation.json()["running"])
                paused = client.put(
                    "/api/tracks/simulation",
                    headers={"If-Match": simulation.json()["revision"]},
                    json={"running": False},
                )
                self.assertEqual(paused.status_code, 200)
                self.assertFalse(paused.json()["running"])
                self.assertEqual(client.post("/api/tracks/simulation/reset").status_code, 200)


if __name__ == "__main__":
    unittest.main()
