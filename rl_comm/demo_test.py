import numpy as np
from progress.bar import Bar
import gym
import gym_flock
import time
import rl_comm.gnn_fwd as gnn_fwd
from stable_baselines.common.vec_env import SubprocVecEnv
from stable_baselines.common.base_class import BaseRLModel


def make_env():
    keys = ['nodes', 'edges', 'senders', 'receivers']
    env = gym.make("MappingRad-v0")
    env = gym.wrappers.FlattenDictWrapper(env, dict_keys=keys)
    return env


def eval_model(env, model, N, render_mode='none'):
    """
    Evaluate a model against an environment over N games.
    """
    results = {'reward': np.zeros(N)}
    with Bar('Eval', max=N) as bar:
        for k in range(N):
            done = False
            obs = env.reset()
            # Run one game.
            while not done:
                action, states = model.predict(obs, deterministic=True)
                obs, rewards, done, info = env.step(action)
                env.render(mode=render_mode)

                if render_mode == 'human':
                    time.sleep(0.5)

                # Record results.
                results['reward'][k] += rewards

            bar.next()
    return results


if __name__ == '__main__':
    from stable_baselines import PPO2

    env = make_env()
    vec_env = SubprocVecEnv([make_env])

    # Specify pre-trained model checkpoint file.
    # model_name = 'models/2019-09-13/2019-09-22/ckpt/ckpt_050.pkl'
    # model_name = 'models/2020-01-20/2020-01-20/ckpt/ckpt_002.pkl'

    model_name = 'ckpt_032.pkl'
    policy_param = {'num_processing_steps': 10}
    n_steps = 32

    new_model = PPO2(
        policy=gnn_fwd.GnnFwd,
        policy_kwargs=policy_param,
        env=vec_env,
        learning_rate=1e-6,
        cliprange=1.0,
        n_steps=n_steps,
        ent_coef=0.0001,
        vf_coef=0.5,
        verbose=1,
        full_tensorboard_log=False)

    # load the dictionary of parameters from file
    _, params = BaseRLModel._load_from_file(model_name)

    # update new model's parameters
    new_model.load_parameters(params)

    print('\nPlay 100 games and return scores...')
    results = eval_model(env, new_model, 10, render_mode='none')
    print('reward,          mean = {:.1f}, std = {:.1f}'.format(np.mean(results['reward']), np.std(results['reward'])))
    print('')

    print('\nPlay games with live visualization...')
    eval_model(env, new_model, 3, render_mode='human')  # also support ffmpeg
