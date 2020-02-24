from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from graph_nets import modules
from graph_nets.blocks import unsorted_segment_max_or_zero
from graph_nets import utils_tf
import sonnet as snt
import tensorflow as tf
from graph_nets import graphs
from stable_baselines.a2c.utils import ortho_init

# NUM_LAYERS = 2  # Hard-code number of layers in the edge/node/global models.
# LATENT_SIZE = 8  # Hard-code latent layer sizes for demos.

NUM_LAYERS = 2
LATENT_SIZE = 16


def make_mlp_model():
    """Instantiates a new MLP, followed by LayerNorm.

    The parameters of each new MLP are not shared with others generated by
    this function.

    Returns:
      A Sonnet module which contains the MLP and LayerNorm.
    """
    return snt.Sequential([
        snt.nets.MLP([LATENT_SIZE] * NUM_LAYERS, activate_final=True, activation=tf.tanh, use_bias=False), snt.LayerNorm()
    ])


def make_linear_model():
    """Instantiates a new linear model.
    Returns:
      A Sonnet module which contains the linear layer.
    """
    return snt.nets.MLP([LATENT_SIZE], activate_final=False, use_bias=False)


class AggregationNet(snt.AbstractModule):
    """
    Aggregation Net with learned aggregation filter
    """

    def __init__(self,
                 num_processing_steps,
                 edge_output_size=None,
                 node_output_size=None,
                 global_output_size=None,
                 name="AggregationNet"):
        super(AggregationNet, self).__init__(name=name)

        self._use_globals = False if global_output_size is None else True
        # core_func = make_linear_model
        self._proc_hops = num_processing_steps
        core_func = make_mlp_model

        # self._proc_hops = [[1] * 2, [2]*(num_processing_steps - 2)]  # [1, 1, 2, 2, 2]
        # self._proc_hops = [item for sublist in self._proc_hops for item in sublist]

        self._core = modules.GraphNetwork(
            edge_model_fn=core_func,
            node_model_fn=core_func,
            global_model_fn=core_func,
            edge_block_opt={'use_receiver_nodes': False, 'use_globals': self._use_globals},
            node_block_opt={'use_globals': self._use_globals},
            name="graph_net"
            # , reducer=unsorted_segment_max_or_zero
        )

        self._encoder = modules.GraphIndependent(make_mlp_model, make_mlp_model, make_mlp_model, name="encoder")
        self._decoder = modules.GraphIndependent(make_mlp_model, make_mlp_model, make_mlp_model, name="decoder")
        self._aggregation = modules.GraphIndependent(make_mlp_model, make_mlp_model, make_mlp_model, name="agg")

        self._num_processing_steps = num_processing_steps
        self._n_stacked = LATENT_SIZE * self._num_processing_steps

        edge_inits = {'w': ortho_init(1.0), 'b': tf.constant_initializer(0.0)}
        global_inits = {'w': ortho_init(1.0), 'b': tf.constant_initializer(0.0)}

        # Transforms the outputs into the appropriate shapes.
        edge_fn = None if edge_output_size is None else lambda: snt.Linear(edge_output_size, initializers=edge_inits,
                                                                           name="edge_output")
        node_fn = None if node_output_size is None else lambda: snt.Linear(node_output_size, initializers=edge_inits,
                                                                           name="node_output")
        global_fn = None if global_output_size is None else lambda: snt.Linear(global_output_size,
                                                                               initializers=global_inits,
                                                                               name="global_output")

        with self._enter_variable_scope():
            self._output_transform = modules.GraphIndependent(edge_fn, node_fn, global_fn, name="output")

    def _build(self, input_op):
        receivers = input_op.receivers
        senders = input_op.senders
        n_node = input_op.n_node
        n_edge = input_op.n_edge

        latent = self._encoder(input_op)
        # latent0 = latent
        output_ops = []

        # proc_hops = [1, 1, 2, 2, 2]  # 1 hop, 2 hop, 4 hop, 8 hop

        for i in range(self._num_processing_steps):

            # for j in range(proc_hops[i]):
            #     core_input = utils_tf.concat([latent0, latent], axis=1)
            #     latent = self._core(core_input)
            # decoded_op = self._decoder(latent)
            # output_ops.append(decoded_op)

            for j in range(self._proc_hops[i]):
                # core_input = utils_tf.concat([latent0, latent], axis=1)
                latent = self._core(latent)

            decoded_op = self._decoder(latent)
            output_ops.append(decoded_op)

        stacked_edges = tf.stack([g.edges for g in output_ops], axis=1)
        stacked_nodes = tf.stack([g.nodes for g in output_ops], axis=1)
        stacked_globals = tf.stack([g.globals for g in output_ops], axis=1)

        stacked_globals = tf.reshape(stacked_globals, (-1, self._n_stacked))
        stacked_edges = tf.reshape(stacked_edges, (-1, self._n_stacked))
        stacked_nodes = tf.reshape(stacked_nodes, (-1, self._n_stacked))

        feature_graph = graphs.GraphsTuple(
            nodes=stacked_nodes,
            edges=stacked_edges,
            globals=stacked_globals,
            receivers=receivers,
            senders=senders,
            n_node=n_node,
            n_edge=n_edge)
        out = self._output_transform(self._aggregation(feature_graph))

        return out


class AggregationDiffNet(snt.AbstractModule):
    """
    Aggregation Net with learned aggregation filter
    """

    def __init__(self,
                 num_processing_steps,
                 edge_output_size=None,
                 node_output_size=None,
                 global_output_size=None,
                 name="AggregationNet"):
        super(AggregationDiffNet, self).__init__(name=name)

        self._use_globals = False if global_output_size is None else True
        # self._proc_hops = [[1] * 3, [3]*(num_processing_steps - 3)]  # [1, 1, 2, 2, 2]
        # self._proc_hops = [[1] * 2, [2]*(num_processing_steps - 2)]  # [1, 1, 2, 2, 2]
        # self._proc_hops = [item for sublist in self._proc_hops for item in sublist]
        # self._proc_hops = num_processing_steps  #[1, 1, 2, 2, 2]
        self._proc_hops = [1, 1, 2, 2, 2]
        # self._proc_hops = [1, 2, 3, 4, 3, 2, 1]

        self._num_processing_steps = len(self._proc_hops)
        self._n_stacked = LATENT_SIZE * self._num_processing_steps

        # core_func = make_linear_model
        core_func = make_mlp_model
        self._cores = []
        for i in range(self._num_processing_steps):

            core = modules.GraphNetwork(
                edge_model_fn=core_func,
                node_model_fn=core_func,
                global_model_fn=core_func,
                edge_block_opt={'use_receiver_nodes': False, 'use_globals': self._use_globals},
                node_block_opt={'use_globals': self._use_globals},
                name="graph_net"
                # , reducer=unsorted_segment_max_or_zero
            )
            self._cores.append(core)

        self._encoder = modules.GraphIndependent(make_mlp_model, make_mlp_model, make_mlp_model, name="encoder")
        self._decoder = modules.GraphIndependent(make_mlp_model, make_mlp_model, make_mlp_model, name="decoder")
        self._aggregation = modules.GraphIndependent(make_mlp_model, make_mlp_model, make_mlp_model, name="agg")

        edge_inits = {'w': ortho_init(1.0), 'b': tf.constant_initializer(0.0)}
        global_inits = {'w': ortho_init(1.0), 'b': tf.constant_initializer(0.0)}

        # Transforms the outputs into the appropriate shapes.
        edge_fn = None if edge_output_size is None else lambda: snt.Linear(edge_output_size, initializers=edge_inits,
                                                                           name="edge_output")
        node_fn = None if node_output_size is None else lambda: snt.Linear(node_output_size, initializers=edge_inits,
                                                                           name="node_output")
        global_fn = None if global_output_size is None else lambda: snt.Linear(global_output_size,
                                                                               initializers=global_inits,
                                                                               name="global_output")

        with self._enter_variable_scope():
            self._output_transform = modules.GraphIndependent(edge_fn, node_fn, global_fn, name="output")

    def _build(self, input_op):
        receivers = input_op.receivers
        senders = input_op.senders
        n_node = input_op.n_node
        n_edge = input_op.n_edge
        latent = self._encoder(input_op)
        output_ops = []

        for i in range(self._num_processing_steps):
            for j in range(self._proc_hops[i]):
                latent = self._cores[i](latent)

            decoded_op = self._decoder(latent)
            output_ops.append(decoded_op)
            # output_ops.append(latent)

        stacked_edges = tf.stack([g.edges for g in output_ops], axis=1)
        stacked_nodes = tf.stack([g.nodes for g in output_ops], axis=1)
        stacked_globals = tf.stack([g.globals for g in output_ops], axis=1)

        stacked_globals = tf.reshape(stacked_globals, (-1, self._n_stacked))
        stacked_edges = tf.reshape(stacked_edges, (-1, self._n_stacked))
        stacked_nodes = tf.reshape(stacked_nodes, (-1, self._n_stacked))

        feature_graph = graphs.GraphsTuple(
            nodes=stacked_nodes,
            edges=stacked_edges,
            globals=stacked_globals,
            receivers=receivers,
            senders=senders,
            n_node=n_node,
            n_edge=n_edge)
        out = self._output_transform(self._aggregation(feature_graph))

        return out
