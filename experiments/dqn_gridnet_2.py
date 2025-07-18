
import argparse
import os
import random
import subprocess
import time
from distutils.util import strtobool
from typing import List
from collections import deque

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from gym.spaces import MultiDiscrete
from stable_baselines3.common.vec_env import VecEnvWrapper, VecMonitor, VecVideoRecorder
from torch.distributions.categorical import Categorical
from torch.utils.tensorboard import SummaryWriter
from wandb.cli.cli import agent

from gym_microrts import microrts_ai
from gym_microrts.envs.vec_env import MicroRTSGridModeVecEnv




def parse_args():
    # fmt: off
    parser = argparse.ArgumentParser()
    parser.add_argument('--exp-name', type=str, default=os.path.basename(__file__).rstrip(".py"),
        help='the name of this experiment')
    parser.add_argument('--gym-id', type=str, default="MicroRTSGridModeVecEnv",
        help='the id of the gym environment')
    parser.add_argument('--learning-rate', type=float, default=2.5e-4,
        help='the learning rate of the optimizer')
    parser.add_argument('--seed', type=int, default=1,
        help='seed of the experiment')
    parser.add_argument('--total-timesteps', type=int, default=50000000,
        help='total timesteps of the experiments')
    parser.add_argument('--torch-deterministic', type=lambda x: bool(strtobool(x)), default=True, nargs='?', const=True,
        help='if toggled, `torch.backends.cudnn.deterministic=False`')
    parser.add_argument('--cuda', type=lambda x: bool(strtobool(x)), default=True, nargs='?', const=True,
        help='if toggled, cuda will not be enabled by default')
    parser.add_argument('--prod-mode', type=lambda x: bool(strtobool(x)), default=False, nargs='?', const=True,
        help='run the script in production mode and use wandb to log outputs')
    parser.add_argument('--capture-video', type=lambda x: bool(strtobool(x)), default=False, nargs='?', const=True,
        help='whether to capture videos of the agent performances (check out `videos` folder)')
    parser.add_argument('--wandb-project-name', type=str, default="gym-microrts",
        help="the wandb's project name")
    parser.add_argument('--wandb-entity', type=str, default=None,
        help="the entity (team) of wandb's project")

    # Algorithm specific arguments
    # ergänzt
    parser.add_argument('--epsilon-start', type=float, default=1.0,
                        help='Startwert für epsilon im epsilon-greedy Ansatz')
    parser.add_argument('--epsilon-final', type=float, default=0.02,
                        help='Minimaler epsilon-Wert')
    parser.add_argument('--epsilon-decay', type=int, default=100000,
                        help='Anzahl der Frames für linearen Epsilon-Zerfall')
    parser.add_argument('--sync-interval', type=int, default=1000,
                        help='Intervall in Frames zum Synchronisieren der Target-Netzwerke')
    # bis hier
    parser.add_argument('--partial-obs', type=lambda x: bool(strtobool(x)), default=False, nargs='?', const=True,
        help='if toggled, the game will have partial observability')
    parser.add_argument('--n-minibatch', type=int, default=4,
        help='the number of mini batch')
    parser.add_argument('--num-bot-envs', type=int, default=0,
        help='the number of bot game environment; 16 bot envs means 16 games')
    parser.add_argument('--num-selfplay-envs', type=int, default=24,
        help='the number of self play envs; 16 self play envs means 8 games')
    parser.add_argument('--num-steps', type=int, default=256,
        help='the number of steps per game environment')
    parser.add_argument('--gamma', type=float, default=0.99,
        help='the discount factor gamma')
    parser.add_argument('--gae-lambda', type=float, default=0.95,
        help='the lambda for the general advantage estimation')
    parser.add_argument('--ent-coef', type=float, default=0.01,
        help="coefficient of the entropy")
    parser.add_argument('--vf-coef', type=float, default=0.5,
        help="coefficient of the value function")
    parser.add_argument('--max-grad-norm', type=float, default=0.5,
        help='the maximum norm for the gradient clipping')
    parser.add_argument('--clip-coef', type=float, default=0.1,
        help="the surrogate clipping coefficient")
    parser.add_argument('--update-epochs', type=int, default=4,
        help="the K epochs to update the policy")
    parser.add_argument('--kle-stop', type=lambda x: bool(strtobool(x)), default=False, nargs='?', const=True,
        help='If toggled, the policy updates will be early stopped w.r.t target-kl')
    parser.add_argument('--kle-rollback', type=lambda x: bool(strtobool(x)), default=False, nargs='?', const=True,
        help='If toggled, the policy updates will roll back to previous policy if KL exceeds target-kl')
    parser.add_argument('--target-kl', type=float, default=0.03,
        help='the target-kl variable that is referred by --kl')
    parser.add_argument('--gae', type=lambda x: bool(strtobool(x)), default=True, nargs='?', const=True,
        help='Use GAE for advantage computation')
    parser.add_argument('--norm-adv', type=lambda x: bool(strtobool(x)), default=True, nargs='?', const=True,
        help="Toggles advantages normalization")
    parser.add_argument('--anneal-lr', type=lambda x: bool(strtobool(x)), default=True, nargs='?', const=True,
        help="Toggle learning rate annealing for policy and value networks")
    parser.add_argument('--clip-vloss', type=lambda x: bool(strtobool(x)), default=True, nargs='?', const=True,
        help='Toggles whether or not to use a clipped loss for the value function, as per the paper.')
    parser.add_argument('--num-models', type=int, default=100,
        help='the number of models saved')
    parser.add_argument('--max-eval-workers', type=int, default=4,
        help='the maximum number of eval workers (skips evaluation when set to 0)')
    parser.add_argument('--train-maps', nargs='+', default=["maps/8x8/basesWorkers8x8.xml"],
        help='the list of maps used during training')
    parser.add_argument('--eval-maps', nargs='+', default=["maps/8x8/basesWorkers8x8.xml"],
        help='the list of maps used during evaluation')

    args = parser.parse_args()
    if not args.seed:
        args.seed = int(time.time())
    args.num_envs = args.num_selfplay_envs + args.num_bot_envs
    args.batch_size = int(args.num_envs * args.num_steps)
    args.minibatch_size = int(args.batch_size // args.n_minibatch)
    args.num_updates = args.total_timesteps // args.batch_size
    args.save_frequency = max(1, int(args.num_updates // args.num_models))
    # fmt: on
    return args


class MicroRTSStatsRecorder(VecEnvWrapper):
    """Nimmt eine Vektorisierte Umgebung und fügt Auswertungstools ein"""
    def __init__(self, env, gamma=0.99) -> None:
        super().__init__(env)
        self.gamma = gamma        # gamma ist unser discount faktor

    def reset(self):
        obs = self.venv.reset()
        self.raw_rewards = [[] for _ in range(self.num_envs)]
        self.ts = np.zeros(self.num_envs, dtype=np.float32)
        self.raw_discount_rewards = [[] for _ in range(self.num_envs)]
        return obs

    def step_wait(self):
        """obs reward, dones werden unverändert zurückgegeben und nur infos in newinfos ungewandeld
        """
        obs, rews, dones, infos = self.venv.step_wait()  #observation
        newinfos = list(infos[:])
        for i in range(len(dones)):
            self.raw_rewards[i] += [infos[i]["raw_rewards"]]
            self.raw_discount_rewards[i] += [
                (self.gamma ** self.ts[i])
                * np.concatenate((infos[i]["raw_rewards"], infos[i]["raw_rewards"].sum()), axis=None)
            ]
            self.ts[i] += 1
            if dones[i]:  #wenn Episode zu Ende
                info = infos[i].copy()
                raw_returns = np.array(self.raw_rewards[i]).sum(0)
                raw_names = [str(rf) for rf in self.rfs]
                raw_discount_returns = np.array(self.raw_discount_rewards[i]).sum(0)
                raw_discount_names = ["discounted_" + str(rf) for rf in self.rfs] + ["discounted"]
                info["microrts_stats"] = dict(zip(raw_names, raw_returns))
                info["microrts_stats"].update(dict(zip(raw_discount_names, raw_discount_returns)))
                self.raw_rewards[i] = []
                self.raw_discount_rewards[i] = []
                self.ts[i] = 0
                newinfos[i] = info
        return obs, rews, dones, newinfos

def to_scalar(x):
    """Konvertiert NumPy-Array, Tensor oder float/int zu float."""
    try:
        if hasattr(x, 'mean'):
            return float(x.mean())
        return float(x)
    except Exception as e:
        print(f"[WARN] to_scalar failed for {x}: {e}")
        return 0.0

def log_training_status(episode_idx, frame_idx, reward, mean_reward, epsilon, start_time):
    reward_val = to_scalar(reward)
    mean_val = to_scalar(mean_reward)
    eps_val = to_scalar(epsilon)
    sps = frame_idx / (time.time() - start_time + 1e-8)  # Schutz gegen Division durch 0

    print(
        f"[Episode {episode_idx:4d}] "
        f"Frame {frame_idx:7d} | "
        f"Reward: {reward_val:.2f} | "
        f"Mean(100): {mean_val:.2f} | "
        f"Epsilon: {eps_val:.2f} | "
        f"SPS: {sps:.2f}"
    )

def get_headwise_action_mask(env, actions_shape, head_config):
    """
    Erzeugt eine Aktionsmaske pro Head (move, attack, produce, etc.).

    Args:
        env: MicroRTS-Environment mit `get_action_mask()`-Methode
        actions_shape: Form des Action-Tensors, z. B. [B, H, W, 7]
        head_config: Dict wie in DQN verwendet, mit "type_id" und "indices"

    Returns:
        mask_dict: Dict mit bool-Masken für jeden Headname (B, H, W, A)
    """
    action_mask = env.get_action_mask()  # [B, H, W, 6, max_param]
    B, H, W, num_components, max_param = action_mask.shape
    mask_dict = {}

    for name, config in head_config.items():
        type_id = config["type_id"]
        indices = config["indices"]

        # move, attack, etc. haben typischerweise eine Parametermaske an indices[1]
        # produce hat zwei: indices[1] = dir, indices[2] = unit
        if name == "produce":
            dir_mask = action_mask[:, :, :, type_id, indices[1]]  # z. B. Richtung (4er)
            unit_mask = action_mask[:, :, :, type_id, indices[2]]  # z. B. Einheitentyp (2er)
            mask_dict[name] = (dir_mask, unit_mask)
        else:
            param_mask = action_mask[:, :, :, type_id, indices[1]]
            mask_dict[name] = param_mask  # [B, H, W, num_options]

    return mask_dict


class ExperienceBuffer:
    def __init__(self, capacity):
        """
        Initialisiert den Replay Buffer mit einer festen Kapazität.
        Neuere Einträge überschreiben automatisch die ältesten (FIFO).
        """
        self.buffer = deque(maxlen=capacity)

    def __len__(self):
        """
        Gibt die aktuelle Anzahl gespeicherter Transitionen zurück.
        """
        return len(self.buffer)

    def append(self, experience):
        """
        Fügt eine neue Erfahrung zum Buffer hinzu.
        Erwartet ein Tuple: (state, action, reward, done, next_state)
        """
        self.buffer.append(experience)

    def sample(self, batch_size):
        indices = np.random.choice(len(self.buffer), batch_size, replace=False)
        batch = [self.buffer[idx] for idx in indices]
        states, actions, rewards, dones, next_states = zip(*batch)

        states = np.array(states)
        next_states = np.array(next_states)
        actions = np.stack(actions)  # → shape: (B, 448)

        # Rekonstruiere optional [B, H, W, 7]
        B = actions.shape[0]
        #print(actions.shape)
        grid_size = int(np.sqrt(actions.shape[1] // 7))
        #print("Grid_size", grid_size)
        actions = actions.reshape(B, grid_size, grid_size, 7)

        return (
            states,
            actions,
            np.array(rewards, dtype=np.float32),
            np.array(dones, dtype=np.uint8),
            next_states
        )


class MovementHead(nn.Module):
    """Ebenfalls verwendet für Harvest und Return"""

    """Aktuell Getrennte Encoder, um Parameter zu sparen könnten alle den selben Encoder verwenden und anschließen über den Head differenzieren"""
    def __init__(self, in_channels):
        super().__init__()

        self.encoder_decision = nn.Sequential(
            nn.Conv2d(in_channels, 32, kernel_size=3, padding=1),  # H × W sollte gleich bleiben
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),  # H × W bleibt gleich
            nn.ReLU()
        )

        self.decision_head = nn.Conv2d(64, 2, kernel_size=1)  # Output Shape für Decision [B, C, H, W], für jedes Grid 0 oder 1 -> C=2 [0,1]

        self.encoder_dir = nn.Sequential(
            nn.Conv2d(in_channels, 32, kernel_size=3, padding=1),  # H × W sollte gleich bleiben
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),  # H × W bleibt gleich
            nn.ReLU()
        )

        self.dir_head = nn.Conv2d(64, 4, kernel_size=1)  #C=[0-3]

    def forward(self, x):

        x_decision = self.encoder_decision(x)  # Shape bleibt (B, 64, H, W)
        decision_logits = self.decision_head(x_decision)  # → (B, 2, H, W)

        x_dir = self.encoder_dir(x)  # Shape bleibt (B, 64, H, W)
        dir_logits = self.dir_head(x_dir)  # → (B, 2, H, W)

        return decision_logits, dir_logits


class ProduceHead(nn.Module):

    def __init__(self, in_channels):
        super().__init__()

        self.encoder_decision = nn.Sequential(
            nn.Conv2d(in_channels, 32, kernel_size=3, padding=1),  # H × W sollte gleich bleiben
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),  # H × W bleibt gleich
            nn.ReLU()
        )
        self.decision_head = nn.Conv2d(64, 2, kernel_size=1)  # Output Shape für Decision [B, C, H, W], für jedes Grid 0 oder 1 -> C=2 [0,1]

        self.encoder_dir = nn.Sequential(
            nn.Conv2d(in_channels, 32, kernel_size=3, padding=1),  # H × W sollte gleich bleiben
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),  # H × W bleibt gleich
            nn.ReLU()
        )
        self.dir_head = nn.Conv2d(64, 4, kernel_size=1)  # C=[0-3]

        self.encoder_type = nn.Sequential(
            nn.Conv2d(in_channels, 32, kernel_size=3, padding=1),  # H × W sollte gleich bleiben
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),  # H × W bleibt gleich
            nn.ReLU()
        )
        self.type_head = nn.Conv2d(64, 7, kernel_size=1)  # C=[0-6] resource, base, barrack,worker, light, heavy, ranged

    def forward(self, x):
        x_decision = self.encoder_decision(x)
        decision_logits = self.decision_head(x_decision)

        x_dir = self.encoder_dir(x)
        dir_logits = self.dir_head(x_dir)

        x_type = self.encoder_type(x)
        type_logits = self.type_head(x_type)

        return decision_logits, dir_logits, type_logits


class AttackHead(nn.Module):
    """Aktuell identisch zum movement head, unklar wie attack direction codiert ist"""
    def __init__(self, in_channels):
        super().__init__()

        self.encoder_decision = nn.Sequential(
            nn.Conv2d(in_channels, 32, kernel_size=3, padding=1),  # H × W sollte gleich bleiben
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),  # H × W bleibt gleich
            nn.ReLU()
        )

        self.decision_head = nn.Conv2d(64, 2,
                                       kernel_size=1)  # Output Shape für Decision [B, C, H, W], für jedes Grid 0 oder 1 -> C=2 [0,1]

        self.encoder_dir = nn.Sequential(
            nn.Conv2d(in_channels, 32, kernel_size=3, padding=1),  # H × W sollte gleich bleiben
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),  # H × W bleibt gleich
            nn.ReLU()
        )

        self.dir_head = nn.Conv2d(64, 49, kernel_size=1)  # C=[0-3]



    def forward(self, x):

        x_decision = self.encoder_decision(x)  # Shape bleibt (B, 64, H, W)
        decision_logits = self.decision_head(x_decision)  # → (B, 2, H, W)

        x_dir = self.encoder_dir(x)  # Shape bleibt (B, 64, H, W)
        dir_logits = self.dir_head(x_dir)  # → (B, 2, H, W)

        return decision_logits, dir_logits

def sync_target_heads(policy_heads, target_heads):
    for name in policy_heads:
        # 1. Parameter auslesen
        params = policy_heads[name].state_dict()
        # 2. In Zielnetz laden
        target_heads[name].load_state_dict(params)


def merge_actions(
    action_type_grid,  # (E, H, W) – Priorisierte Aktionsauswahl
    attack_params=None,      # (H, W, 2)
    harvest_mask=None,       # (H, W) – bool
    return_mask=None,        # (H, W) – bool
    produce_params=None,     # (H, W, 1)
    production_type=None,
    move_params=None         # (H, W, 1)
):
    """
    Erstellt einen flachen Aktionsvektor aus den Einzel-Head-Ausgaben.
    action_type_ bestimmt, welche Aktion pro Grid aktiv ist.
    Die restlichen Parameter werden gesetzt, wenn die Aktion aktiv ist.
    Format: 7 Einträge pro Zelle, wie in MicroRTS erwartet.
    """

    E ,H, W = action_type_grid.shape
    full_action = np.zeros((E, H, W, 7), dtype=np.int32)
    #print("produce_params.shape:", produce_params.shape)
    for i in range(E):
        for j in range(H):
            for k in range(W):
                a_type = action_type_grid[i,j,k] #holt sich den action type
                full_action[i,j,k, 0] = a_type  # action type eintragen

                # Aktion ausführen, andere Parameterfelder auf 0
                if a_type == 5 and attack_params is not None:  #action_type=5 -> Attack
                    #print("attack_params.shape:", attack_params.shape)
                    #print("Beispielwert:", attack_params[i, j, k])
                    full_action[i,j,k,6]=attack_params[i,j,k]
                    #print("Attack")

                elif a_type == 2 and harvest_mask is not None:
                    full_action[i, j, k, 2] = harvest_mask[i, j, k]
                    #print("Harvest")

                elif a_type == 3 and return_mask is not None:
                    full_action[i, j, k, 3] = return_mask[i, j, k]
                    #print("retuern")

                elif a_type == 4 and produce_params is not None and production_type is not None:
                    full_action[i, j, k, 4] = produce_params[i, j, k]
                    #full_action[i, j, k, 5] = production_type[i, j, k]
                    print("Produce")

                elif a_type == 1 and move_params is not None:
                    full_action[i, j, k, 1] = move_params[i, j, k]
                #print("move")



    return full_action.reshape(E, H * W * 7)

def get_action_type_grid(attack_decision,
    harvest_decision,
    return_decision,
    produce_decision,
    move_decision):
    #print("Attack_decision_shape:", attack_decision.shape) #Attack_decision_shape: (24, 8, 8)
    E, H, W = attack_decision.shape
    #print(E, H,W)
    #print(attack_decision.shape)


    action_type_grid = np.full((E,H, W), 6, dtype=np.int32)
    for i in range(E):
        for j in range(H):
            for k in range(W):
                """Regelt Priorisierung der Decider
                Attack>Harvest>return>Produce>move"""
                if attack_decision[i,j,k] == 1:
                    action_type_grid[i,j,k] = 5
                elif harvest_decision[i,j,k] == 1:
                    action_type_grid[i,j,k] = 2
                elif return_decision[i,j,k] == 1:
                    action_type_grid[i,j,k] = 3
                elif produce_decision[i,j,k] == 1:
                    action_type_grid[i,j,k] = 4
                elif move_decision[i,j,k] == 1:
                    action_type_grid[i,j,k] = 1

    return action_type_grid


class Agent:
    def __init__(self, env, exp_buffer, device="cpu"):
        """
        Initialisiert den Agenten mit Zugriff auf die Umgebung und den Replay Buffer.
        """
        self.device=device
        self.env = env
        self.exp_buffer = exp_buffer
        self._reset()

        self.movement_head = MovementHead(in_channels=29).to(self.device)
        self.harvest_head = MovementHead(in_channels=29).to(self.device)
        self.return_head = MovementHead(in_channels=29).to(self.device)
        self.production_head = ProduceHead(in_channels=29).to(self.device)
        self.attack_head = AttackHead(in_channels=29).to(self.device)

        self.heads = {
            "attack": self.attack_head,
            "harvest": self.harvest_head,
            "return": self.return_head,
            "move": self.movement_head,
            "produce": self.production_head,
        }

        self.head_config = {
            "attack": {"type_id": 5, "indices": (0, 6), "classes": (2, 4)},
            "harvest": {"type_id": 2, "indices": (0, 2), "classes": (2, 4)},        # type_id: Acion cpmponentn action type id
            "return": {"type_id": 3, "indices": (0, 3), "classes": (2, 4)},         # indices: Relevanten Action components
            "produce": {"type_id": 4, "indices": (0, 4, 5), "classes": (2, 4, 7)},  #classes: Decider, Direction, Production_type
            "move": {"type_id": 1, "indices": (0, 1), "classes": (2, 4)} # classes: Decider, Direction
        }

    def _get_structured_action_masks(self, state, device):
        raw_masks = self.env.venv.venv.get_action_mask()  # [num_envs, b*h, 78]
        grid_size = int(np.sqrt(raw_masks.shape[1]))

        def reshape_and_convert(mask, channels):
            mask = mask.reshape(self.env.num_envs, grid_size, grid_size, channels)
            return torch.tensor(mask, dtype=torch.float32, device=device).permute(0, 3, 1, 2)

        return {
            "move_dir": reshape_and_convert(raw_masks[:, :, 6:10], 4),
            "harvest_dir": reshape_and_convert(raw_masks[:, :, 10:14], 4),
            "return_dir": reshape_and_convert(raw_masks[:, :, 14:18], 4),
            "produce_dir": reshape_and_convert(raw_masks[:, :, 18:22], 4),
            "produce_type": reshape_and_convert(raw_masks[:, :, 22:29], 7),
            "attack_dir": reshape_and_convert(raw_masks[:, :, 29:78], 49),
        }


    def _reset(self):
        """
        Startet eine neue Episode und setzt interne Zustände zurück.
        """
        self.state = self.env.reset()
        self.total_reward = 0.0



    @torch.no_grad()
    def play_step(self, epsilon=0.0, device="cpu"):
        """
                for idx in range(64):
            valid = np.sum(raw_mask[0, idx])
            if valid > 0:
                #print(f"Index {idx} gültig mit {valid} Aktionen")

        for i in range(8):
            for j in range(8):
                base = (i * 8 + j) * 6
                print(f"Zelle ({i},{j}) → Index {base}")
        """

        """
        Führt einen Schritt im Environment aus:
        - Wählt eine Aktion mittels ε-greedy Strategie
        - Führt Aktion im Environment aus
        - Speichert Transition im Replay Buffer
        - Rückgabe: Gesamt-Reward bei Episodenende, sonst None
        """
        done_reward = None

        # ε-greedy Aktionsauswahl
        if np.random.random() < epsilon:
            raw_masks = self.env.venv.venv.get_action_mask()  # [num_envs, H*W, 78]
            grid_size = int(np.sqrt(raw_masks.shape[1]))

            def sample_valid(mask_2d):
                valid_indices = np.where(mask_2d)[0]
                if len(valid_indices) == 0:
                    return 0  # Fallback auf 0
                return np.random.choice(valid_indices)

            action = np.zeros((self.env.num_envs, grid_size, grid_size, 7), dtype=np.int32)
            for env_i in range(self.env.num_envs):
                for idx in range(grid_size * grid_size):
                    cell_mask = raw_masks[env_i, idx]
                    i, j = divmod(idx, grid_size)

                    a_type = sample_valid(cell_mask[0:6])
                    action[env_i, i, j, 0] = a_type

                    if a_type == 1:  # Move
                        action[env_i, i, j, 1] = sample_valid(cell_mask[6:10])
                    elif a_type == 2:  # Harvest
                        action[env_i, i, j, 2] = sample_valid(cell_mask[10:14])
                    elif a_type == 3:  # Return
                        action[env_i, i, j, 3] = sample_valid(cell_mask[14:18])
                    elif a_type == 4:  # Produce
                        action[env_i, i, j, 4] = sample_valid(cell_mask[18:22])
                        action[env_i, i, j, 5] = sample_valid(cell_mask[22:29])
                    elif a_type == 5:  # Attack
                        action[env_i, i, j, 6] = sample_valid(cell_mask[29:78])

            action = action.reshape(self.env.num_envs, -1)
        else:
            # Zustand vorbereiten für Netzwerkeingabe
            state_a = np.array(self.state, copy=False)
            state_v = torch.tensor(self.state, dtype=torch.float32, device=self.device).permute(0, 3, 1, 2)


            """Jeder Kopf muss alle seine maximal Möglichen Aktionen machen, diese einzeln. Die beste Aktion an Merge 
            schicken, welcher die Gesamtaktion ausführt
            """
            # Berechne strukturierte Aktionsmasken
            masks = self._get_structured_action_masks(self.state, device=self.device)

            # Attack
            attack_decision, attack_dir = self.attack_head(state_v)
            attack_mask = attack_decision.argmax(dim=1).cpu().numpy()
            attack_dir = attack_dir.masked_fill(masks["attack_dir"] == 0, -1e8)
            attack_param = attack_dir.argmax(dim=1).cpu().numpy()

            # Move
            move_decision, move_dir = self.movement_head(state_v)
            move_mask = move_decision.argmax(dim=1).cpu().numpy()
            move_dir = move_dir.masked_fill(masks["move_dir"] == 0, -1e8)
            move_param = move_dir.argmax(dim=1).cpu().numpy()

            # Harvest
            harvest_decision, harvest_dir = self.harvest_head(state_v)
            harvest_mask = harvest_decision.argmax(dim=1).cpu().numpy()
            harvest_dir = harvest_dir.masked_fill(masks["harvest_dir"] == 0, -1e8)
            harvest_param = harvest_dir.argmax(dim=1).cpu().numpy()

            # Return
            return_decision, return_dir = self.return_head(state_v)
            return_mask = return_decision.argmax(dim=1).cpu().numpy()
            return_dir = return_dir.masked_fill(masks["return_dir"] == 0, -1e8)
            return_param = return_dir.argmax(dim=1).cpu().numpy()

            # Produce
            production_decision, production_dir, production_type = self.production_head(state_v)
            produce_mask = production_decision.argmax(dim=1).cpu().numpy()
            production_dir = production_dir.masked_fill(masks["produce_dir"] == 0, -1e8)
            production_type = production_type.masked_fill(masks["produce_type"] == 0, -1e8)
            produce_param = production_dir.argmax(dim=1).cpu().numpy()
            produce_type = production_type.argmax(dim=1).cpu().numpy()

            #Führe Teilaktion zur Gesamtaktion zusammen
            action_type_grid=get_action_type_grid(attack_mask,harvest_mask, return_mask, produce_mask, move_mask)
            """
            
            print("attack_mask.shape:", attack_mask.shape)
            print("move_mask.shape:", move_mask.shape)
            print("state_v.shape:", state_v.shape)
            """

            action=merge_actions(action_type_grid,attack_param,harvest_param,return_param, produce_type,produce_param,move_param)
            #print("doppelcheck", action.shape)

            #Führe Aktion aus
        torch.tensor(self.env.venv.venv.get_action_mask(), dtype=torch.float32)
        new_state, reward, is_done, _= self.env.step(action)
        self.total_reward += reward

        #print("action.shape before storing:", action.shape)


        for env_i in range(self.env.num_envs):
            self.exp_buffer.append((
                self.state[env_i],
                action[env_i],  # → shape: (448,)
                reward[env_i],
                is_done[env_i],
                new_state[env_i]
            ))

        self.state = new_state
        if np.any(is_done):
            done_reward = self.total_reward
            self._reset()
            return done_reward
        return None

    def calc_loss(self, batch, target_heads, gamma=0.99):
        #print("calc_loss")
        states, actions, rewards, dones, next_states = batch
        device = self.device
        total_loss = 0.0

        # Tensor-Konvertierung
        states_t = torch.tensor(states, dtype=torch.float32, device=device).permute(0, 3, 1, 2)
        next_states_t = torch.tensor(next_states, dtype=torch.float32, device=device).permute(0, 3, 1, 2)
        rewards_t = torch.tensor(rewards, dtype=torch.float32, device=device)
        dones_t = torch.tensor(dones, dtype=torch.bool, device=device)
        actions = torch.tensor(actions, dtype=torch.long, device=device)  # [B, H, W, 7]

        B, H, W = actions.shape[:3]

        # Einmalige Netzwerkausgaben pro Head
        outputs_by_head = {name: head(states_t) for name, head in self.heads.items()}
        targets_by_head = {name: target_heads[name](next_states_t) for name in self.heads}
        head_configs = {name: self.head_config[name] for name in self.heads}

        q_preds = []
        q_tgts = []

        for b in range(B):
            for i in range(H):
                for j in range(W):
                    action_type = actions[b, i, j, 0].item()

                    # Finde zuständigen Head über type_id
                    for name, cfg in head_configs.items():
                        if cfg["type_id"] == action_type:
                            indices = cfg["indices"]
                            outputs = outputs_by_head[name]
                            targets = targets_by_head[name]

                            reward = rewards_t[b]
                            done = dones_t[b]

                            if name == "produce":
                                action_dir = actions[b, i, j, indices[1]].item()
                                action_prod = actions[b, i, j, indices[2]].item()

                                q_val_dir = outputs[1][b, action_dir, i, j]
                                q_val_prod = outputs[2][b, action_prod, i, j]

                                q_target_dir = targets[1][b, :, i, j].max()
                                q_target_prod = targets[2][b, :, i, j].max()

                                q_preds.append(q_val_dir)
                                q_preds.append(q_val_prod)

                                q_tgts.append(reward + gamma * q_target_dir * (1.0 - (~done).float()))
                                q_tgts.append(reward + gamma * q_target_prod * (1.0 - (~done).float()))

                            else:
                                action_param = actions[b, i, j, indices[1]].item()   #indices[0]->0->action type ; indices[1]-> action param
                                q_val = outputs[1][b, action_param, i, j]
                                q_target = targets[1][b, :, i, j].max()

                                q_preds.append(q_val)
                                q_tgts.append(reward + gamma * q_target * ((~done).float()))

                            break  # Head gefunden, andere ignorieren

        if q_preds and q_tgts:
            q_preds_t = torch.stack(q_preds)
            q_tgts_t = torch.stack(q_tgts)
            total_loss = F.mse_loss(q_preds_t, q_tgts_t)

        #print(f"Loss-Einträge: {len(q_preds)}")
        return total_loss


"""
Observation shape:  ([24, 8, 8, 29]) #[num_env, H,W, C]
move_dir.shape:     ([1, 4, 8, 8])  c=[0,3]  
move_dec.shape:     ([1, 2, 8, 8])  c=[0,1]

"""
if __name__ == "__main__":

    print(">>> Argumentparser wird initialisiert")
    args = parse_args()

    print(f"Save frequency: {args.save_frequency}")

    # TRY NOT TO MODIFY: setup the environment
    experiment_name = f"{args.gym_id}__{args.exp_name}__{args.seed}__{int(time.time())}"
    if args.prod_mode:
        import wandb

        run = wandb.init(
            project=args.wandb_project_name,
            entity=args.wandb_entity,
            # sync_tensorboard=True,
            config=vars(args),
            name=experiment_name,
            monitor_gym=True,
            save_code=True,
        )
        wandb.tensorboard.patch(save=False)
    writer = SummaryWriter(f"runs/{experiment_name}")
    writer.add_text(
        "hyperparameters", "|param|value|\n|-|-|\n%s" % ("\n".join([f"|{key}|{value}|" for key, value in vars(args).items()]))
    )

    # TRY NOT TO MODIFY: seeding
    device = torch.device("cuda" if torch.cuda.is_available() and args.cuda else "cpu")

    print(f"Device: {device}")

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.backends.cudnn.deterministic = args.torch_deterministic
    envs = MicroRTSGridModeVecEnv(
        num_selfplay_envs=args.num_selfplay_envs,
        num_bot_envs=args.num_bot_envs,
        partial_obs=args.partial_obs,
        max_steps=2000,
        render_theme=2,
        ai2s=[microrts_ai.coacAI for _ in range(args.num_bot_envs - 6)]
             + [microrts_ai.randomBiasedAI for _ in range(min(args.num_bot_envs, 2))]
             + [microrts_ai.lightRushAI for _ in range(min(args.num_bot_envs, 2))]
             + [microrts_ai.workerRushAI for _ in range(min(args.num_bot_envs, 2))],
        map_paths=[args.train_maps[0]],
        reward_weight=np.array([10.0, 1.0, 1.0, 0.2, 1.0, 4.0]),
        cycle_maps=args.train_maps,
    )
    envs = MicroRTSStatsRecorder(envs, args.gamma)
    envs = VecMonitor(envs)
    if args.capture_video:
        envs = VecVideoRecorder(
            envs, f"videos/{experiment_name}", record_video_trigger=lambda x: x % 100000 == 0, video_length=2000
        )
    assert isinstance(envs.action_space, MultiDiscrete), "only MultiDiscrete action space is supported"

    eval_executor = None
    if args.max_eval_workers > 0:
        from concurrent.futures import ThreadPoolExecutor

        eval_executor = ThreadPoolExecutor(max_workers=args.max_eval_workers, thread_name_prefix="league-eval-")
    """
    

    obs = envs.reset()
    expbuffer = ExperienceBuffer(100)
    envs.render(mode="human")
    _ = envs.venv.venv.get_action_mask()  # Initialisiere die source_unit_mask
    agent = Agent(envs, expbuffer, device=device)



    for i in range(200):
        test = agent.play_step(epsilon=0.5)
    print("Agent erfolgreich getestet")

    print(test)

    envs.venv.venv.render(mode="human")


    #teste replay Buffer

    for _ in range(200):  # ein paar Schritte generieren
        agent.play_step( epsilon=0.5, device=device)

    # Automatische Ableitung der expected_shape für actions: (H, W, 7)
    obs_shape = envs.observation_space.shape  # z. B. (8, 8, 29)
    grid_h, grid_w = obs_shape[:2]
    expected_action_shape = (grid_h, grid_w, 7)
    print(expected_action_shape)

    test_replay_buffer_once(expbuffer, expected_shape=expected_action_shape, batch_size=64)




    target_heads = {name: head for name, head in agent.heads.items()}
    # 3. Mini-Batch ziehen
    batch = expbuffer.sample(batch_size=64)

    # 4. Loss berechnen
    loss = agent.calc_loss(batch, target_heads)
    print("Loss:", loss)


    input("Drücke Enter, um die Umgebung zu schließen...")
    """



    """
    Training
    """
    # Trainingsteil am Ende deines Skripts ergänzen oder in main() kapseln

    expbuffer = ExperienceBuffer(capacity=10000)
    agent = Agent(envs, expbuffer, device=device)
    target_heads = {name: head for name, head in agent.heads.items()}

    optimizer = optim.Adam(
        [p for head in agent.heads.values() for p in head.parameters()],
        lr=args.learning_rate
    )

    total_rewards = []
    frame_idx = 0
    episode_idx = 0
    best_mean_reward = None
    epsilon = args.epsilon_start
    start_time = time.time()
    log_interval=1000
    mean_reward = 0.0  # vor der Trainingsschleife definieren

    reward_queue = deque(maxlen=100)  # Vor der Schleife
    print("Starte Training")
    start_time = time.time()
    while frame_idx < args.total_timesteps:
        frame_idx += 1
        epsilon = max(args.epsilon_final, args.epsilon_start - frame_idx / args.epsilon_decay)

        reward = agent.play_step(epsilon=epsilon, device=device)
        #envs.venv.venv.render(mode="human")
        if frame_idx % log_interval == 0:
            log_training_status(episode_idx, frame_idx, reward, mean_reward, epsilon, start_time)


        if reward is not None:
            episode_idx += 1
            total_rewards.append(reward)
            reward_queue.append(reward)

            mean_reward = np.mean(total_rewards[-100:])
            print(f"Episode {episode_idx}, Frame {frame_idx}: "
                  f"Mean(100)={mean_reward:.2f}, Epsilon={epsilon:.2f}")

            if best_mean_reward is None or best_mean_reward < mean_reward:
                print(f"Neues bestes Ergebnis: {best_mean_reward} → {mean_reward:.2f}")
                best_mean_reward = mean_reward
                for name, head in agent.heads.items():
                    torch.save(head.state_dict(), f"{args.exp_name}_{name}_best.pth")

        if len(expbuffer) < args.batch_size:
            continue

        if frame_idx % args.sync_interval == 0:
            sync_target_heads(agent.heads, target_heads)

        batch = expbuffer.sample(args.batch_size)
        optimizer.zero_grad()
        loss = agent.calc_loss(batch, target_heads, gamma=args.gamma)
        loss.backward()
        optimizer.step()

        # Logging
        if frame_idx % 1000 ==0:
            print("frame index:", frame_idx)
            print("Loss:", loss)

        if frame_idx % 10000 == 0:
            for name, head in agent.heads.items():
                torch.save(head.state_dict(), f"checkpoints/{args.exp_name}_{name}_{frame_idx}.pth")

        writer.add_scalar("charts/epsilon", epsilon, frame_idx)
        if len(reward_queue) > 0:
            writer.add_scalar("charts/mean_100_ep_reward", np.mean(reward_queue), frame_idx)

    print("Training abgeschlossen.")

    envs.close()
