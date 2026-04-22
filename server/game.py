"""Pure Pong game logic. No asyncio, no I/O — easy to unit test.

Playfield is 1.0 wide and 1.0 tall. Player 1's paddle sits at the bottom
(y near 1.0), player 2's at the top (y near 0.0). Ball moves in this
canonical space; clients flip the view for player 2.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, asdict
from typing import Literal

Status = Literal["countdown", "playing", "paused", "game_over"]

BALL_SIZE = 0.045
PADDLE_WIDTH = 0.18
PADDLE_HALF = PADDLE_WIDTH / 2
PADDLE_THICKNESS = 0.025
P1_PADDLE_Y = 0.75
P2_PADDLE_Y = 0.25
INITIAL_SPEED = 0.275
SPEED_BUMP = 1.05
MAX_SPEED = 0.65
MAX_SCORE = 5
COUNTDOWN_SECONDS = 3


@dataclass
class Ball:
    x: float = 0.5
    y: float = 0.5
    vx: float = 0.0
    vy: float = 0.0


@dataclass
class GameState:
    ball: Ball = field(default_factory=Ball)
    p1_x: float = 0.5
    p2_x: float = 0.5
    score_p1: int = 0
    score_p2: int = 0
    status: Status = "countdown"
    countdown_remaining: float = float(COUNTDOWN_SECONDS)
    winner: int = 0
    last_sfx: str = ""


class PongGame:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self.state = GameState()
        self._reset_ball(toward_p1=self._rng.choice([True, False]))

    def apply_input(self, slot: int, paddle_x: float) -> None:
        x = max(PADDLE_HALF, min(1.0 - PADDLE_HALF, float(paddle_x)))
        if slot == 1:
            self.state.p1_x = x
        elif slot == 2:
            self.state.p2_x = x

    def start_countdown(self) -> None:
        self.state.status = "countdown"
        self.state.countdown_remaining = float(COUNTDOWN_SECONDS)

    def pause(self) -> None:
        self.state.status = "paused"

    def resume_from_pause(self) -> None:
        if self.state.status == "paused":
            self.start_countdown()

    def rematch(self) -> None:
        self.state.score_p1 = 0
        self.state.score_p2 = 0
        self.state.winner = 0
        self._reset_ball(toward_p1=self._rng.choice([True, False]))
        self.start_countdown()

    def step(self, dt: float) -> None:
        self.state.last_sfx = ""
        if self.state.status == "countdown":
            self.state.countdown_remaining -= dt
            if self.state.countdown_remaining <= 0.0:
                self.state.countdown_remaining = 0.0
                self.state.status = "playing"
            return
        if self.state.status != "playing":
            return

        b = self.state.ball
        b.x += b.vx * dt
        b.y += b.vy * dt

        if b.x - BALL_SIZE / 2 < 0.0:
            b.x = BALL_SIZE / 2
            b.vx = abs(b.vx)
            self.state.last_sfx = "wall"
        elif b.x + BALL_SIZE / 2 > 1.0:
            b.x = 1.0 - BALL_SIZE / 2
            b.vx = -abs(b.vx)
            self.state.last_sfx = "wall"

        if b.vy > 0 and self._hits_paddle(b, self.state.p1_x, P1_PADDLE_Y):
            b.y = P1_PADDLE_Y - PADDLE_THICKNESS / 2 - BALL_SIZE / 2
            self._bounce_off_paddle(b, self.state.p1_x)
            b.vy = -abs(b.vy)
            self.state.last_sfx = "paddle"
        elif b.vy < 0 and self._hits_paddle(b, self.state.p2_x, P2_PADDLE_Y):
            b.y = P2_PADDLE_Y + PADDLE_THICKNESS / 2 + BALL_SIZE / 2
            self._bounce_off_paddle(b, self.state.p2_x)
            b.vy = abs(b.vy)
            self.state.last_sfx = "paddle"

        if b.y > P1_PADDLE_Y:
            self.state.score_p2 += 1
            self.state.last_sfx = "goal"
            self._after_goal(scorer=2)
        elif b.y < P2_PADDLE_Y:
            self.state.score_p1 += 1
            self.state.last_sfx = "goal"
            self._after_goal(scorer=1)

    @staticmethod
    def _hits_paddle(b: Ball, paddle_x: float, paddle_y: float) -> bool:
        y_overlap = (
            b.y + BALL_SIZE / 2 >= paddle_y - PADDLE_THICKNESS / 2
            and b.y - BALL_SIZE / 2 <= paddle_y + PADDLE_THICKNESS / 2
        )
        x_overlap = abs(b.x - paddle_x) <= PADDLE_HALF + BALL_SIZE / 2
        return y_overlap and x_overlap

    @staticmethod
    def _bounce_off_paddle(b: Ball, paddle_x: float) -> None:
        offset = (b.x - paddle_x) / PADDLE_HALF
        offset = max(-1.0, min(1.0, offset))
        speed = math.hypot(b.vx, b.vy) * SPEED_BUMP
        speed = min(speed, MAX_SPEED)
        angle = offset * (math.pi / 3)
        b.vx = speed * math.sin(angle)
        b.vy = speed * math.cos(angle)  # magnitude only; caller sets vy sign.

    def _after_goal(self, scorer: int) -> None:
        if max(self.state.score_p1, self.state.score_p2) >= MAX_SCORE:
            self.state.status = "game_over"
            self.state.winner = 1 if self.state.score_p1 >= MAX_SCORE else 2
            self.state.last_sfx = "game_over"
            return
        self._reset_ball(toward_p1=(scorer == 2))
        self.start_countdown()

    def _reset_ball(self, toward_p1: bool) -> None:
        b = self.state.ball
        b.x = 0.5
        b.y = 0.5
        angle = self._rng.uniform(-math.pi / 4.5, math.pi / 4.5)
        direction_y = 1.0 if toward_p1 else -1.0
        b.vx = INITIAL_SPEED * math.sin(angle)
        b.vy = direction_y * INITIAL_SPEED * math.cos(angle)

    def snapshot(self) -> dict:
        s = self.state
        out = {
            "ball": asdict(s.ball),
            "p1_x": s.p1_x,
            "p2_x": s.p2_x,
            "score_p1": s.score_p1,
            "score_p2": s.score_p2,
            "status": s.status,
            "countdown_remaining": round(s.countdown_remaining, 2),
            "winner": s.winner,
        }
        if s.last_sfx:
            out["sfx"] = s.last_sfx
        return out
