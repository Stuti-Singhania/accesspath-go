"use client";

import { useEffect } from "react";
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, useMap, useMapEvents } from "react-leaflet";
import type { Barrier, PickMode, RouteSummary } from "@/types";

type Props = {
  mode: PickMode;
  start: [number, number] | null;
  destination: [number, number] | null;
  reportPoint: [number, number] | null;
  route: RouteSummary | null;
  reports: Barrier[];
  onPick: (mode: PickMode, point: [number, number]) => void;
};

function ClickPicker({ mode, onPick }: Pick< Props, "mode" | "onPick">) {
  useMapEvents({ click(event) { onPick(mode, [event.latlng.lng, event.latlng.lat]); } });
  return null;
}

function Recenter({ point }: { point: [number, number] | null }) {
  const map = useMap();
  useEffect(() => { if (point) map.flyTo([point[1], point[0]], 15, { duration: 0.6 }); }, [point, map]);
  return null;
}

export default function RouteMap({ mode, start, destination, reportPoint, route, reports, onPick }: Props) {
  const focus = start ?? destination ?? reportPoint;
  const polyline = route?.geometry.coordinates.map(([lon, lat]) => [lat, lon] as [number, number]) ?? [];
  return (
    <MapContainer center={focus ? [focus[1], focus[0]] : [18, 0]} zoom={focus ? 14 : 2} scrollWheelZoom className="map-canvas" aria-label="Map: click to set the selected map point">
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <ClickPicker mode={mode} onPick={onPick} />
      <Recenter point={focus} />
      {start && <CircleMarker center={[start[1], start[0]]} radius={9} color="#fff" weight={3} fillColor="#2e6f55" fillOpacity={1}><Popup>Walk starts here</Popup></CircleMarker>}
      {destination && <CircleMarker center={[destination[1], destination[0]]} radius={9} color="#fff" weight={3} fillColor="#d47c3e" fillOpacity={1}><Popup>Walk destination</Popup></CircleMarker>}
      {reportPoint && <CircleMarker center={[reportPoint[1], reportPoint[0]]} radius={8} color="#fff" weight={3} fillColor="#754d9a" fillOpacity={1}><Popup>New report location</Popup></CircleMarker>}
      {polyline.length > 1 && <Polyline positions={polyline} color="#2e6f55" weight={6} opacity={0.9} />}
      {reports.map((report) => (
        <CircleMarker key={report.id} center={[report.latitude, report.longitude]} radius={7}
          color="#fff" weight={2} fillColor={report.severity === "high" ? "#b8503e" : "#d1973d"} fillOpacity={1}>
          <Popup><strong>{report.barrier_type.replaceAll("_", " ")}</strong><br />{report.severity} severity<br />{report.description}</Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  );
}
