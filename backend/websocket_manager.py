"""
WebSocket 連線管理：支援多位護理師同時監控
"""
from fastapi import WebSocket
import json


class NurseAlertManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_alert(self, alert_data: dict):
        """廣播緊急提醒給所有在線護理師"""
        message = json.dumps(alert_data, ensure_ascii=False)
        dead = []
        for conn in self.active_connections:
            try:
                await conn.send_text(message)
            except Exception:
                dead.append(conn)
        for conn in dead:
            self.active_connections.remove(conn)

    @property
    def connection_count(self) -> int:
        return len(self.active_connections)


manager = NurseAlertManager()
