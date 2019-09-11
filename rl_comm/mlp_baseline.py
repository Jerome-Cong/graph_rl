import numpy as np
import sonnet as snt
import tensorflow as tf

from graph_nets import graphs, modules, blocks
from stable_baselines.common.policies import ActorCriticPolicy
from stable_baselines.common.policies import mlp_extractor
from stable_baselines.a2c.utils import linear

from gym_pdefense.envs import pdefense_env

def mlp_model_fn(layers, default, activate_final):
    """
    Return model_fn for mlp, or default if len(layers) == 0. Typical
    defaults are None or lambda: tf.identity.
    """
    if len(layers) != 0:
        model_fn=lambda: snt.nets.MLP(layers, activate_final=activate_final)
    else:
        model_fn=default
    return model_fn

class MyMlpPolicy(ActorCriticPolicy):
    """

    Policy object that implements actor critic, using a a vanilla centralized
    MLP (2 layers of 64).

    :param sess: (TensorFlow session) The current TensorFlow session
    :param ob_space: (Gym Space) The observation space of the environment
    :param ac_space: (Gym Space) The action space of the environment
    :param n_env: (int) The number of environments to run
    :param n_steps: (int) The number of steps to run for each environment
    :param n_batch: (int) The number of batch to run (n_envs * n_steps)
    :param reuse: (bool) If the policy is reusable or not
    :param net_arch: (list) Specification of the actor-critic policy network architecture (see mlp_extractor
        documentation for details).
    """

    def __init__(self, sess, ob_space, ac_space, n_env, n_steps, n_batch, reuse=False,
        net_arch=[dict(vf=[64,64], pi=[64,64])],
        w_agent=1,
        w_target=2,
        w_obs=2):

        super(MyMlpPolicy, self).__init__(sess, ob_space, ac_space, n_env, n_steps, n_batch, reuse,
                                                scale=False)

        n_agents = ac_space.nvec.size
        n_targets = (np.prod(ob_space.shape) - n_agents**2 - n_agents*w_agent) // (n_agents + w_target + n_agents*w_obs)
        assert np.prod(ob_space.shape) == n_agents**2 + n_agents*w_agent + n_agents*n_targets + n_targets*w_target + n_agents*n_targets*w_obs, 'Broken game size computation.'

        (comm_adj, agent_node_data, obs_adj, target_node_data, obs_edge_data) = \
            pdefense_env.unpack_obs_graph_coord_tf(self.processed_obs, n_agents, n_targets, w_agent, w_target, w_obs)
        obs = tf.concat((tf.layers.flatten(agent_node_data), tf.layers.flatten(obs_edge_data)), axis=1)

        with tf.variable_scope("model", reuse=reuse):
            # Shared latent representation across entire team.
            pi_latent, vf_latent = mlp_extractor(obs, net_arch, tf.nn.relu)

            self._value_fn = linear(vf_latent, 'vf', 1)

            self._proba_distribution, self._policy, self.q_value = \
                self.pdtype.proba_distribution_from_latent(pi_latent, vf_latent, init_scale=0.01)

        self._setup_init()

    def step(self, obs, state=None, mask=None, deterministic=False):
        if deterministic:
            action, value, neglogp = self.sess.run([self.deterministic_action, self.value_flat, self.neglogp],
                                                   {self.obs_ph: obs})
        else:
            action, value, neglogp = self.sess.run([self.action, self.value_flat, self.neglogp],
                                                   {self.obs_ph: obs})
        return action, value, self.initial_state, neglogp

    def proba_step(self, obs, state=None, mask=None):
        return self.sess.run(self.policy_proba, {self.obs_ph: obs})

    def value(self, obs, state=None, mask=None):
        return self.sess.run(self.value_flat, {self.obs_ph: obs})




class OneNodePolicy(ActorCriticPolicy):
    """

    Policy object that implements actor critic, using the GraphNet API to
    reproduce the vanilla centralized MLP result (2 layers of 64)).

    :param sess: (TensorFlow session) The current TensorFlow session
    :param ob_space: (Gym Space) The observation space of the environment
    :param ac_space: (Gym Space) The action space of the environment
    :param n_env: (int) The number of environments to run
    :param n_steps: (int) The number of steps to run for each environment
    :param n_batch: (int) The number of batch to run (n_envs * n_steps)
    :param reuse: (bool) If the policy is reusable or not
    :param net_arch: (list) Specification of the actor-critic policy network architecture (see mlp_extractor
        documentation for details).
    """

    def __init__(self, sess, ob_space, ac_space, n_env, n_steps, n_batch, reuse=False,
        net_arch=[dict(vf=[64,64], pi=[64,64])],
        w_agent=1,
        w_target=2,
        w_obs=2):

        super(OneNodePolicy, self).__init__(sess, ob_space, ac_space, n_env, n_steps, n_batch, reuse,
                                                scale=False)

        n_agents = ac_space.nvec.size
        n_targets = (np.prod(ob_space.shape) - n_agents**2 - n_agents*w_agent) // (n_agents + w_target + n_agents*w_obs)
        assert np.prod(ob_space.shape) == n_agents**2 + n_agents*w_agent + n_agents*n_targets + n_targets*w_target + n_agents*n_targets*w_obs, 'Broken game size computation.'

        (comm_adj, agent_node_data, obs_adj, target_node_data, obs_edge_data) = \
            pdefense_env.unpack_obs_graph_coord_tf(self.processed_obs, n_agents, n_targets, w_agent, w_target, w_obs)

        # Build observation graph. Concatenate all agent data and observation
        # data into a single node, and include no edges.
        B = tf.shape(obs_adj)[0]
        N = obs_adj.shape[1]
        nodes = tf.concat((tf.layers.flatten(agent_node_data), tf.layers.flatten(obs_edge_data)), axis=1)
        n_node = tf.fill((B,), 1)
        n_edge = tf.fill((B,), 0)
        in_graph = graphs.GraphsTuple(
            nodes=nodes,
            edges=None,
            globals=None,
            receivers=None,
            senders=None,
            n_node=n_node,
            n_edge=n_edge)

        with tf.variable_scope("model", reuse=reuse):

            # Transform the single node's data.
            state_mlp = blocks.NodeBlock(
                node_model_fn=lambda: snt.nets.MLP(tuple(net_arch[0]['pi']) + (N*2,), activate_final=False),
                use_received_edges=False,
                use_sent_edges=False,
                use_nodes=True,
                use_globals=False,
                name="pi_state_mlp")
            pi_g = state_mlp(in_graph)

            # Transform the single node's data.
            state_mlp = blocks.NodeBlock(
                node_model_fn=lambda: snt.nets.MLP(net_arch[0]['vf'], activate_final=True),
                use_received_edges=False,
                use_sent_edges=False,
                use_nodes=True,
                use_globals=False,
                name="vf_state_mlp")
            vf_g = state_mlp(in_graph)

            # Reduce to single global value.
            vf_state_agg = blocks.GlobalBlock(
                global_model_fn=lambda: snt.Linear(output_size=1),
                use_nodes=True,
                use_edges=False,
                use_globals=False,
                name='vf_state_agg')
            state_value_g = vf_state_agg(vf_g)

            # Reduce to per-agent action values. Not needed by A2C.
            vf_action_agg = blocks.NodeBlock(
                node_model_fn=lambda: snt.Linear(output_size=2),
                use_received_edges=False,
                use_sent_edges=False,
                use_nodes=True,
                use_globals=False,
                name='vf_action_agg')
            action_value_g = vf_action_agg(vf_g)

            # Team value.
            self._value_fn = state_value_g.globals
            self.q_value   = tf.reshape(action_value_g.nodes, (B, N*2))
            # Team policy.
            self._policy = tf.reshape(pi_g.nodes, (B, N*2))
            self._proba_distribution = self.pdtype.proba_distribution_from_flat(self._policy)

        self._setup_init()

    def step(self, obs, state=None, mask=None, deterministic=False):
        if deterministic:
            action, value, neglogp = self.sess.run([self.deterministic_action, self.value_flat, self.neglogp],
                                                   {self.obs_ph: obs})
        else:
            action, value, neglogp = self.sess.run([self.action, self.value_flat, self.neglogp],
                                                   {self.obs_ph: obs})
        return action, value, self.initial_state, neglogp

    def proba_step(self, obs, state=None, mask=None):
        return self.sess.run(self.policy_proba, {self.obs_ph: obs})

    def value(self, obs, state=None, mask=None):
        return self.sess.run(self.value_flat, {self.obs_ph: obs})