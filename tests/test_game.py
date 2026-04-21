"""Tests for server/game.py physics and scoring."""

from __future__ import annotations

import random

from server.game import (
    BALL_SIZE,
    COUNTDOWN_SECONDS,
    MAX_SCORE,
    P1_PADDLE_Y,
    P2_PADDLE_Y,
    PADDLE_THICKNESS,
    PongGame,
)


def _advance_past_countdown(g: PongGame) -> None:
    g.step(COUNTDOWN_SECONDS + 0.01)


def test_input_clamped_to_playfield():
    g = PongGame(rng=random.Random(0))
    g.apply_input(1, -5.0)
    assert 0.0 < g.state.p1_x < 1.0
    g.apply_input(2, 99.0)
    assert 0.0 < g.state.p2_x < 1.0


def test_countdown_transitions_to_playing():
    g = PongGame(rng=random.Random(0))
    assert g.state.status == "countdown"
    g.step(1.0)
    assert g.state.status == "countdown"
    g.step(COUNTDOWN_SECONDS)
    assert g.state.status == "playing"


def test_wall_bounce_flips_vx():
    g = PongGame(rng=random.Random(0))
    _advance_past_countdown(g)
    g.state.ball.x = 0.02
    g.state.ball.y = 0.5
    g.state.ball.vx = -0.5
    g.state.ball.vy = 0.0
    g.step(0.1)
    assert g.state.ball.vx > 0
    assert g.state.last_sfx == "wall"


def test_ball_bounces_off_player1_paddle():
    g = PongGame(rng=random.Random(0))
    _advance_past_countdown(g)
    g.state.p1_x = 0.5
    g.state.ball.x = 0.5
    g.state.ball.y = P1_PADDLE_Y - PADDLE_THICKNESS / 2 - BALL_SIZE / 2 - 0.01
    g.state.ball.vx = 0.0
    g.state.ball.vy = 0.5
    g.step(0.1)
    assert g.state.ball.vy < 0, "ball should reverse y after paddle hit"
    assert g.state.last_sfx == "paddle"


def test_p2_scores_when_ball_passes_p1_baseline():
    g = PongGame(rng=random.Random(0))
    _advance_past_countdown(g)
    g.state.p1_x = 0.0  # far away so it definitely misses
    g.state.ball.x = 0.5
    g.state.ball.y = 0.99
    g.state.ball.vx = 0.0
    g.state.ball.vy = 1.0
    g.step(0.1)
    assert g.state.score_p2 == 1
    assert g.state.score_p1 == 0
    assert g.state.status == "countdown"


def test_p1_scores_when_ball_passes_p2_baseline():
    g = PongGame(rng=random.Random(0))
    _advance_past_countdown(g)
    g.state.p2_x = 0.0
    g.state.ball.x = 0.5
    g.state.ball.y = 0.01
    g.state.ball.vx = 0.0
    g.state.ball.vy = -1.0
    g.step(0.1)
    assert g.state.score_p1 == 1
    assert g.state.score_p2 == 0


def test_first_to_max_score_triggers_game_over():
    g = PongGame(rng=random.Random(0))
    g.state.score_p1 = MAX_SCORE - 1
    _advance_past_countdown(g)
    g.state.p2_x = 0.0
    g.state.ball.x = 0.5
    g.state.ball.y = 0.01
    g.state.ball.vx = 0.0
    g.state.ball.vy = -1.0
    g.step(0.1)
    assert g.state.status == "game_over"
    assert g.state.winner == 1


def test_rematch_resets_scores_and_starts_countdown():
    g = PongGame(rng=random.Random(0))
    g.state.score_p1 = 4
    g.state.score_p2 = 5
    g.state.status = "game_over"
    g.state.winner = 2
    g.rematch()
    assert g.state.score_p1 == 0
    assert g.state.score_p2 == 0
    assert g.state.winner == 0
    assert g.state.status == "countdown"


def test_snapshot_contains_expected_fields():
    g = PongGame(rng=random.Random(0))
    snap = g.snapshot()
    for key in ["ball", "p1_x", "p2_x", "score_p1", "score_p2", "status", "countdown_remaining", "winner"]:
        assert key in snap
    assert set(snap["ball"].keys()) == {"x", "y", "vx", "vy"}
