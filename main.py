import socket
import struct
import paho.mqtt.client as mqtt
import json
import os

BROKER = os.environ.get("MQTT_BROKER", "raspberrypi.local")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
TOPIC = os.environ.get("MQTT_TOPIC", "f1/telemetry")

client = mqtt.Client()
client.connect(BROKER, PORT, keepalive=60)
client.loop_start()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "127.0.0.1"

UDP_IP = get_local_ip()
UDP_PORT = 20777

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print(f"Listening for F1 2024 telemetry on {UDP_IP}:{UDP_PORT}...")

def parse_packet_header(data):
    header_format = "<HBBBBBQfIIBB"
    if len(data) < 29:
        raise ValueError("Data too short for PacketHeader.")
    fields = struct.unpack(header_format, data[:29])
    return {
        "packet_format": fields[0],
        "game_year": fields[1],
        
        "game_major_version": fields[2],
        "game_minor_version": fields[3],
        "packet_version": fields[4],
        "packet_id": fields[5],
        "session_uid": fields[6],
        "session_time": fields[7],
        "frame_identifier": fields[8],
        "overall_frame_identifier": fields[9],
        "player_car_index": fields[10],
        "secondary_player_car_index": fields[11],
    }

def parse_motion_packet(data):
    offset = 29
    car_motion_format = "<fff" + "fff" + "hhh" + "hhh" + "fff" + "fff"
    car_motion_size = struct.calcsize(car_motion_format)
    car_motion_data = {}

    for i in range(22):
        start = offset + i * car_motion_size
        end = start + car_motion_size
        fields = struct.unpack(car_motion_format, data[start:end])
        
        car_data = {
            "worldPositionX": fields[0],
            "worldPositionY": fields[1],
            "worldPositionZ": fields[2],
            "worldVelocityX": fields[3],
            "worldVelocityY": fields[4],
            "worldVelocityZ": fields[5],
            "worldForwardDirX": fields[6],
            "worldForwardDirY": fields[7],
            "worldForwardDirZ": fields[8],
            "worldRightDirX": fields[9],
            "worldRightDirY": fields[10],
            "worldRightDirZ": fields[11],
            "gForceLateral": fields[12],
            "gForceLongitudinal": fields[13],
            "gForceVertical": fields[14],
            "yaw": fields[15],
            "pitch": fields[16],
            "roll": fields[17],
        }

        car_motion_data["Car" + str(i)] = car_data

    return car_motion_data

def parse_telemetry_packet(data):
    offset = 29
    car_telemetry_format = "<HfffBbhBBH" + "4H" + "4B" + "4B" + "H" + "4f" + "4B"
    car_telemetry_size = struct.calcsize(car_telemetry_format)
    car_telemetry_data = {}

    for i in range(22):
        start = offset + i * car_telemetry_size
        end = start + car_telemetry_size
        fields = struct.unpack(car_telemetry_format, data[start:end])
        
        car_data = {
            "speed": fields[0],
            "throttle": fields[1],
            "steer": fields[2],
            "brake": fields[3],
            "clutch": fields[4],
            "gear": fields[5],
            "engineRPM": fields[6],
            "drs": fields[7],
            "revLightsPercent": fields[8],
            "revLightsBitValue": fields[9],
            "brakesTemperature": list(fields[10:14]),
            "tyresSurfaceTemperature": list(fields[14:18]),
            "tyresInnerTemperature": list(fields[18:22]),
            "engineTemperature": fields[22],
            "tyresPressure": list(fields[23:27]),
            "surfaceType": list(fields[27:31]),
        }

        car_telemetry_data["Car" + str(i)] = car_data

    return car_telemetry_data

def parse_session_history_packet(data):
    offset = 29
    session_history = {}

    # Car and metadata
    m_carIdx, m_numLaps, m_numTyreStints, bestLap, bestS1, bestS2, bestS3 = struct.unpack_from("<BBBBBBB", data, offset)
    session_history["carIndex"] = m_carIdx
    session_history["numLaps"] = m_numLaps
    session_history["numTyreStints"] = m_numTyreStints
    session_history["bestLapNum"] = bestLap
    session_history["bestSector1LapNum"] = bestS1
    session_history["bestSector2LapNum"] = bestS2
    session_history["bestSector3LapNum"] = bestS3

    offset += 7

    # LapHistoryData struct: 12 bytes each
    lap_history_data = []
    for _ in range(100):
        lap_data = struct.unpack_from("<IHBH BHB B", data, offset)
        offset += 12
        lap_history_data.append({
            "lapTimeMS": lap_data[0],
            "sector1MS": lap_data[1],
            "sector1Min": lap_data[2],
            "sector2MS": lap_data[3],
            "sector2Min": lap_data[4],
            "sector3MS": lap_data[5],
            "sector3Min": lap_data[6],
            "lapValidFlags": lap_data[7]
        })

    session_history["lapHistoryData"] = lap_history_data

    # TyreStintHistoryData struct: 3 bytes each
    tyre_stints = []
    for _ in range(8):
        stint = struct.unpack_from("<BBB", data, offset)
        offset += 3
        tyre_stints.append({
            "endLap": stint[0],
            "actualCompound": stint[1],
            "visualCompound": stint[2]
        })

    session_history["tyreStintsHistory"] = tyre_stints

    return session_history

def parse_event_packet(data):
    offset = 29  # After header
    event_code = data[offset:offset+4].decode("utf-8")
    details_offset = offset + 4

    event_data = {"eventCode": event_code}

    if event_code == "FTLP":  # Fastest Lap
        vehicle_idx, lap_time = struct.unpack_from("<Bf", data, details_offset)
        event_data.update({
            "vehicleIdx": vehicle_idx,
            "lapTime": lap_time
        })
    elif event_code == "RTMT":  # Retirement
        (vehicle_idx,) = struct.unpack_from("<B", data, details_offset)
        event_data.update({
            "vehicleIdx": vehicle_idx
        })
    elif event_code == "TMPT":  # Team mate in pits
        (vehicle_idx,) = struct.unpack_from("<B", data, details_offset)
        event_data.update({
            "vehicleIdx": vehicle_idx
        })
    elif event_code == "RCWN":  # Race winner
        (vehicle_idx,) = struct.unpack_from("<B", data, details_offset)
        event_data.update({
            "vehicleIdx": vehicle_idx
        })
    elif event_code == "PENA":  # Penalty
        fields = struct.unpack_from("<BBBBBBB", data, details_offset)
        event_data.update({
            "penaltyType": fields[0],
            "infringementType": fields[1],
            "vehicleIdx": fields[2],
            "otherVehicleIdx": fields[3],
            "time": fields[4],
            "lapNum": fields[5],
            "placesGained": fields[6],
        })
    elif event_code == "SPTP":  # Speed trap
        vehicle_idx, speed, fastest_overall, fastest_driver, fastest_vehicle_idx, fastest_speed = struct.unpack_from("<BfBBBf", data, details_offset)
        event_data.update({
            "vehicleIdx": vehicle_idx,
            "speed": speed,
            "isOverallFastest": fastest_overall,
            "isDriverFastest": fastest_driver,
            "fastestVehicleIdx": fastest_vehicle_idx,
            "fastestSpeed": fastest_speed
        })
    elif event_code == "STLG":  # Start lights
        (num_lights,) = struct.unpack_from("<B", data, details_offset)
        event_data.update({
            "numLights": num_lights
        })
    elif event_code == "DTSV":  # Drive through served
        (vehicle_idx,) = struct.unpack_from("<B", data, details_offset)
        event_data.update({
            "vehicleIdx": vehicle_idx
        })
    elif event_code == "SGSV":  # Stop go served
        (vehicle_idx,) = struct.unpack_from("<B", data, details_offset)
        event_data.update({
            "vehicleIdx": vehicle_idx
        })
    elif event_code == "FLBK":  # Flashback
        frame_id, session_time = struct.unpack_from("<If", data, details_offset)
        event_data.update({
            "frameIdentifier": frame_id,
            "sessionTime": session_time
        })
    elif event_code == "BUTN":  # Button status
        (button_status,) = struct.unpack_from("<I", data, details_offset)
        event_data.update({
            "buttonStatus": button_status
        })
    elif event_code == "OVTK":  # Overtake
        overtaking_idx, overtaken_idx = struct.unpack_from("<BB", data, details_offset)
        event_data.update({
            "overtakingVehicleIdx": overtaking_idx,
            "beingOvertakenVehicleIdx": overtaken_idx
        })
    elif event_code == "SCAR":  # Safety Car
        safety_type, event_type = struct.unpack_from("<BB", data, details_offset)
        event_data.update({
            "safetyCarType": safety_type,
            "eventType": event_type
        })
    elif event_code == "COLL":  # Collision
        vehicle1_idx, vehicle2_idx = struct.unpack_from("<BB", data, details_offset)
        event_data.update({
            "vehicle1Idx": vehicle1_idx,
            "vehicle2Idx": vehicle2_idx
        })
    else:
        event_data.update({"info": "Unknown event"})

    return event_data

def parse_lap_data_packet(data):
    offset = 29  # Skip header
    lap_data_list = []

    lap_data_format = (
        "<IIH B H B H B H B f f f B B B B B B B B B B B B B B B H H B f B"
    )
    lap_data_size = struct.calcsize(lap_data_format)

    for i in range(22):  # 22 cars
        start = offset + i * lap_data_size
        fields = struct.unpack_from(lap_data_format, data, start)

        lap_data = {
            "lastLapTimeMS": fields[0],
            "currentLapTimeMS": fields[1],
            "sector1MS": fields[2],
            "sector1Min": fields[3],
            "sector2MS": fields[4],
            "sector2Min": fields[5],
            "deltaToCarInFrontMS": fields[6],
            "deltaToCarInFrontMin": fields[7],
            "deltaToLeaderMS": fields[8],
            "deltaToLeaderMin": fields[9],
            "lapDistance": fields[10],
            "totalDistance": fields[11],
            "safetyCarDelta": fields[12],
            "carPosition": fields[13],
            "currentLapNum": fields[14],
            "pitStatus": fields[15],
            "numPitStops": fields[16],
            "sector": fields[17],
            "currentLapInvalid": fields[18],
            "penalties": fields[19],
            "totalWarnings": fields[20],
            "cornerCuttingWarnings": fields[21],
            "unservedDriveThroughPens": fields[22],
            "unservedStopGoPens": fields[23],
            "gridPosition": fields[24],
            "driverStatus": fields[25],
            "resultStatus": fields[26],
            "pitLaneTimerActive": fields[27],
            "pitLaneTimeInLaneMS": fields[28],
            "pitStopTimerMS": fields[29],
            "pitStopShouldServePen": fields[30],
            "speedTrapFastestSpeed": fields[31],
            "speedTrapFastestLap": fields[32],
        }

        lap_data_list.append(lap_data)

    # Get the final 2 time trial indices
    tt_pb_idx = struct.unpack_from("<B", data, offset + 22 * lap_data_size)[0]
    tt_rival_idx = struct.unpack_from("<B", data, offset + 22 * lap_data_size + 1)[0]

    return {
        "lapData": lap_data_list,
        "timeTrialPBIndex": tt_pb_idx,
        "timeTrialRivalIndex": tt_rival_idx
    }

def parse_session_packet(data):
    offset = 29  # Skip the header
    
    # Unpack fixed-size base fields before marshal zones
    base_format = (
        "<"           # Little-endian
        "BbbBHbBbbHHBBBB"      # Up to m_sliProNativeSupport
        "B"                    # m_numMarshalZones
    )
    base_size = struct.calcsize(base_format)
    fields = struct.unpack_from(base_format, data, offset)

    session_data = {
        "weather": fields[0],
        "trackTemperature": fields[1],
        "airTemperature": fields[2],
        "totalLaps": fields[3],
        "trackLength": fields[4],
        "sessionType": fields[5],
        "trackId": fields[6],
        "formula": fields[7],
        "sessionTimeLeft": fields[8],
        "sessionDuration": fields[9],
        "pitSpeedLimit": fields[10],
        "gamePaused": fields[11],
        "isSpectating": fields[12],
        "spectatorCarIndex": fields[13],
        "sliProNativeSupport": fields[14],
        "numMarshalZones": fields[15],
    }

    offset += base_size

    # Parse marshal zones (21 max)
    marshal_zones = []
    for _ in range(21):
        zone = struct.unpack_from("<fb", data, offset)
        marshal_zones.append({
            "zoneStart": zone[0],
            "zoneFlag": zone[1]
        })
        offset += struct.calcsize("<fb")
    session_data["marshalZones"] = marshal_zones

    # Next fields
    tail_format = (
        "<"  # Continue in little-endian
        "BBB"   # safetyCarStatus, networkGame, numWeatherForecastSamples
    )
    tail_fields = struct.unpack_from(tail_format, data, offset)
    session_data.update({
        "safetyCarStatus": tail_fields[0],
        "networkGame": tail_fields[1],
        "numWeatherForecastSamples": tail_fields[2],
    })
    offset += struct.calcsize(tail_format)

    # Parse weather forecast samples (64 max)
    weather_samples = []
    for _ in range(64):
        sample = struct.unpack_from("<BBBbbbbB", data, offset)
        weather_samples.append({
            "sessionType": sample[0],
            "timeOffset": sample[1],
            "weather": sample[2],
            "trackTemperature": sample[3],
            "trackTemperatureChange": sample[4],
            "airTemperature": sample[5],
            "airTemperatureChange": sample[6],
            "rainPercentage": sample[7],
        })
        offset += struct.calcsize("<BBBbbbbB")
    session_data["weatherForecastSamples"] = weather_samples[:session_data["numWeatherForecastSamples"]]

    # Continue parsing remaining scalar fields
    remaining_format = (
        "<BBIII"      # forecastAccuracy, aiDifficulty, 3x identifiers
        "BBB"         # pitStopWindowIdealLap, LatestLap, RejoinPos
        "BBBBBBBBBBBB"  # Assist settings
        "BBBBBB"       # gameMode, ruleSet, timeOfDay, sessionLength, speed units
        "BBBBBBB"      # more gameplay settings
        "BBBBBBBBBBBB" # mp/online settings
        "BB"           # cornerCuttingStringency, parcFermeRules
        "BB"           # pitStopExperience, safetyCar
        "BB"           # safetyCarExperience, formationLap
        "BB"           # formationLapExperience, redFlags
        "BB"           # affectsLicenceLevelSolo, affectsLicenceLevelMP
        "B"            # numSessionsInWeekend
    )
    remaining_size = struct.calcsize(remaining_format)
    remaining_fields = struct.unpack_from(remaining_format, data, offset)
    offset += remaining_size

    keys = [
        "forecastAccuracy", "aiDifficulty",
        "seasonLinkIdentifier", "weekendLinkIdentifier", "sessionLinkIdentifier",
        "pitStopWindowIdealLap", "pitStopWindowLatestLap", "pitStopRejoinPosition",
        "steeringAssist", "brakingAssist", "gearboxAssist", "pitAssist", "pitReleaseAssist",
        "ERSAssist", "DRSAssist", "dynamicRacingLine", "dynamicRacingLineType",
        "gameMode", "ruleSet", "timeOfDay", "sessionLength",
        "speedUnitsLeadPlayer", "temperatureUnitsLeadPlayer",
        "speedUnitsSecondaryPlayer", "temperatureUnitsSecondaryPlayer",
        "numSafetyCarPeriods", "numVirtualSafetyCarPeriods", "numRedFlagPeriods", "equalCarPerformance",
        "recoveryMode", "flashbackLimit", "surfaceType", "lowFuelMode", "raceStarts",
        "tyreTemperature", "pitLaneTyreSim", "carDamage", "carDamageRate", "collisions",
        "collisionsOffForFirstLapOnly", "mpUnsafePitRelease", "mpOffForGriefing",
        "cornerCuttingStringency", "parcFermeRules",
        "pitStopExperience", "safetyCar", "safetyCarExperience", "formationLap",
        "formationLapExperience", "redFlags", "affectsLicenceLevelSolo", "affectsLicenceLevelMP",
        "numSessionsInWeekend"
    ]
    session_data.update(dict(zip(keys, remaining_fields)))

    # Weekend structure (12 bytes)
    session_data["weekendStructure"] = list(struct.unpack_from("<12B", data, offset))
    offset += 12

    # Final two floats
    session_data["sector2LapDistanceStart"], session_data["sector3LapDistanceStart"] = struct.unpack_from("<ff", data, offset)
    offset += 8

    return session_data


# Main loop
while True:
    data, addr = sock.recvfrom(2048)
    try:
        header = parse_packet_header(data)
        print(f"Packet ID: {header['packet_id']:>2} | Time: {header['session_time']:.2f}")
        client.publish(TOPIC + "/Header", json.dumps(header))

        match header['packet_id']:
            case 0:  # Motion
                motion = parse_motion_packet(data)
                print("Motion:", motion["Car0"])  # Optional: print 1 car
                client.publish(TOPIC + "/Motion", json.dumps({"header": header, "motion": motion}))
            case 6:  # Car Telemetry
                telemetry = parse_telemetry_packet(data)
                print("Telemetry:", telemetry["Car0"])  # Optional: print 1 car
                client.publish(TOPIC + "/car", json.dumps({"data":{"header": header, "car": telemetry}}))
            case 1: # Session
                print("hi")
                session = parse_session_packet(data)
                print("Session Weather:", session["weather"])
                client.publish(TOPIC + "/Session", json.dumps({
                    "header": header,
                    "session": session
                }))

            case 2: # Lap Data
                lap_data = parse_lap_data_packet(data)
                print("Lap Data:", lap_data["lapData"][0])  # Optional: print Car 0 lap data
                client.publish(TOPIC + "/LapData", json.dumps({
                    "header": header,
                    "lapData": lap_data
                }))
            case 3: # Event
                event = parse_event_packet(data)
                print(f"Event: {event['eventCode']} | Details: {event}")
                client.publish(TOPIC + "/Event", json.dumps({
                    "header": header,
                    "event": event
                }))
            case 4: # Participants
                pass
            case 5: # Car Setups
                pass
            case 7: # Car Status
                pass
            case 8: # Final Classification
                pass
            case 9: # Lobby Info
                pass
            case 10: # Car Damage
                pass
            case 11: # Session History
                lap_data = parse_session_history_packet(data)
                print(f"Lap data for Car {lap_data['carIndex']}, total laps: {lap_data['numLaps']}")
                client.publish(TOPIC + "/SessionHistory", json.dumps({"header": header, "sessionHistory": lap_data}))
            case 12: # Tyre Sets
                pass
            case 13: # Motion Ex
                pass
            case 14: # Time Trial
                pass

    except Exception as e:
        print(f"Failed to parse packet: {e}")

client.disconnect()
