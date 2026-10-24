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

            invalid = scenario()
            invalid.routes[0].legs[0].targetSpeedMetresPerSecond = 1_000
            with self.assertRaises(HTTPException) as limit:
                store.update_scenario(invalid, None)
            self.assertEqual(limit.exception.status_code, 422)

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

    def test_opening_workspace_reloads_catalogues_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TrackStore(root / "types.json", root / "scenario.json")
            self.configure_test_catalogues(store)
            store.update_scenario(scenario(), None)
            reloaded_types = aircraft_types()
            reloaded_types.types[0].displayName = "Reloaded test plane"
            store.aircraft_types_path.write_text(
                reloaded_types.model_dump_json(indent=2),
                encoding="utf-8",
            )

            with patch.object(track_service, "track_store", store):
                client = TestClient(track_service.app)
                workspace = client.get("/api/workspace")
                types     = client.get("/api/tracks/aircraft-types")

            self.assertEqual(workspace.status_code, 200)
            self.assertEqual(
                workspace.json()["panes"][1]["summary"]["items"][0]["title"],
                "Reloaded test plane",
            )
            self.assertEqual(types.status_code, 200)
            self.assertEqual(types.json()["types"][0]["displayName"], "Reloaded test plane")

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
