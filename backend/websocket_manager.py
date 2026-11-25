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

    async def broadcast_alert(self, alert_data: dict) -> int:
        """廣播提醒給所有在線的護理端連線，回傳「實際送達」的連線數。

        回傳 0 代表當下沒有任何人在線 —— 呼叫端必須據實記錄（notified_channels=[]），
        不可對病患宣稱「已通知」（2026-11 同儕審查 M2）。
        """
        message = json.dumps(alert_data, ensure_ascii=False)
        dead, delivered = [], 0
        for conn in list(self.active_connections):
            try:
                await conn.send_text(message)
                delivered += 1
            except Exception:
                dead.append(conn)
        for conn in dead:
            self.disconnect(conn)
        return delivered

    @property
    def connection_count(self) -> int:
        return len(self.active_connections)


manager = NurseAlertManager()
