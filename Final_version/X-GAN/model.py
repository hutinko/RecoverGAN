from __future__ import division
import os
import time
import math
from glob import glob
import tensorflow as tf
import numpy as np
from six.moves import xrange
import copy

from ops import *
from utils import *

# This should be considerated again
# The original code is modified from DCGAN implementation:
# https://github.com/carpedm20/DCGAN-tensorflow


def conv_out_size_same(size, stride):
    return int(math.ceil(float(size) / float(stride)))


class DCGAN(object):

    def __init__(self, sess, input_height=64, input_width=64, is_crop=True,
                 batch_size=64, sample_num=64, output_height=64, output_width=64,
                 y_dim=None, z_dim=100, gf_dim=64, df_dim=64,
                 gfc_dim=1024, dfc_dim=1024, c_dim=3, dataset_name='default',
                 input_fname_pattern='*.jpg', checkpoint_dir=None, sample_dir=None, lambda_val=0.08, dataset_name2='default'):

        self.sess = sess
        self.is_crop = is_crop
        self.is_grayscale = (c_dim == 1)

        # For Inpainting
        self.image_size = input_height
        self.image_shape = [input_height, input_width, 3]

        # According to the paper:
        # [Yeh, Raymond, et al. "Semantic Image Inpainting with Perceptual and Contextual Losses." arXiv preprint arXiv:1607.07539 (2016).]
        # labmda value should be relatively small to constrain the recovered
        # image with the input pixels
        self.lambda_val = lambda_val

        self.batch_size = batch_size
        self.sample_num = sample_num

        self.input_height = input_height
        self.input_width = input_width
        self.output_height = output_height
        self.output_width = output_width

        self.y_dim = y_dim
        self.z_dim = z_dim

        # Xz-GAN
        #######################################################################
        self.z_dim = output_height * output_width * 3

        #######################################################################
        self.gf_dim = gf_dim
        self.df_dim = df_dim

        self.gfc_dim = gfc_dim
        self.dfc_dim = dfc_dim
        self.c_dim = c_dim

        # batch normalization : deals with poor initialization helps gradient
        # flow
        self.d_bn1 = batch_norm(name='d_bn1')
        self.d_bn2 = batch_norm(name='d_bn2')

        if not self.y_dim:
            self.d_bn3 = batch_norm(name='d_bn3')

        self.g_bn0 = batch_norm(name='g_bn0')
        self.g_bn1 = batch_norm(name='g_bn1')
        self.g_bn2 = batch_norm(name='g_bn2')

        if not self.y_dim:
            self.g_bn3 = batch_norm(name='g_bn3')

        self.dataset_name = dataset_name

        # Xz-GAN
        #######################################################################
        self.dataset_name2 = dataset_name2
        #######################################################################
        self.input_fname_pattern = input_fname_pattern
        self.checkpoint_dir = checkpoint_dir
        self.build_model()

    def build_model(self):
        if self.y_dim:
            self.y = tf.placeholder(
                tf.float32, [self.batch_size, self.y_dim], name='y')

        if self.is_crop:
            image_dims = [self.output_height, self.output_width, self.c_dim]
        else:
            image_dims = [self.input_height, self.input_width, self.c_dim]

        self.inputs = tf.placeholder(
            tf.float32, [self.batch_size] + image_dims, name='real_images')
        self.sample_inputs = tf.placeholder(
            tf.float32, [self.sample_num] + image_dims, name='sample_inputs')

        inputs = self.inputs
        sample_inputs = self.sample_inputs

        self.z = tf.placeholder(
            tf.float32, [None, self.z_dim], name='z')
        self.z_sum = histogram_summary("z", self.z)

        if self.y_dim:
            self.G = self.generator(self.z, self.y)
            self.D, self.D_logits = self.discriminator(
                inputs, self.y, reuse=False)

            self.sampler = self.sampler(self.z, self.y)
            self.D_, self.D_logits_ = self.discriminator(
                self.G, self.y, reuse=True)
        else:
            self.G = self.generator(self.z)
            self.D, self.D_logits = self.discriminator(inputs)

            self.sampler = self.sampler(self.z)
            self.D_, self.D_logits_ = self.discriminator(self.G, reuse=True)

        self.d_sum = histogram_summary("d", self.D)
        self.d__sum = histogram_summary("d_", self.D_)
        self.G_sum = image_summary("G", self.G)

        def sigmoid_cross_entropy_with_logits(x, y):
            try:
                return tf.nn.sigmoid_cross_entropy_with_logits(logits=x, labels=y)
            except:
                return tf.nn.sigmoid_cross_entropy_with_logits(logits=x, targets=y)

        self.d_loss_real = tf.reduce_mean(
            sigmoid_cross_entropy_with_logits(self.D_logits, tf.ones_like(self.D)))
        self.d_loss_fake = tf.reduce_mean(
            sigmoid_cross_entropy_with_logits(self.D_logits_, tf.zeros_like(self.D_)))
        self.g_loss = tf.reduce_mean(
            sigmoid_cross_entropy_with_logits(self.D_logits_, tf.ones_like(self.D_)))

        self.d_loss_real_sum = scalar_summary("d_loss_real", self.d_loss_real)
        self.d_loss_fake_sum = scalar_summary("d_loss_fake", self.d_loss_fake)

        self.d_loss = self.d_loss_real + self.d_loss_fake

        self.g_loss_sum = scalar_summary("g_loss", self.g_loss)
        self.d_loss_sum = scalar_summary("d_loss", self.d_loss)

        t_vars = tf.trainable_variables()

        self.d_vars = [var for var in t_vars if 'd_' in var.name]
        self.g_vars = [var for var in t_vars if 'g_' in var.name]

        # Wasserstein-GAN
        #######################################################################
        # self.d_loss_real = tf.reduce_mean(self.D_logits)
        # self.d_loss_fake = tf.reduce_mean(self.D_logits_)
        # self.g_loss = -tf.reduce_mean(self.D_logits_)
        # self.d_loss = self.d_loss_real - self.d_loss_fake
        #######################################################################

        self.saver = tf.train.Saver()

        # For Auto Inpainting
        # Refered http://bamos.github.io/2016/08/09/deep-completion/ and paper: with MIT license
        # [Yeh, Raymond, et al. "Semantic Image Inpainting with Perceptual and Contextual Losses." arXiv preprint arXiv:1607.07539 (2016).]
        self.mask = tf.placeholder(
            tf.float32, [None] + self.image_shape, name='mask')

        # Contextual loss
        self.contextual_loss = tf.reduce_sum(
            tf.contrib.layers.flatten(
                tf.abs(tf.multiply(self.mask, self.G) - tf.multiply(self.mask, self.inputs))), 1)

        self.contextual_loss_sum = scalar_summary(
            "contextual_loss_sum", self.contextual_loss)

        # Perceptual loss
        self.perceptual_loss = self.g_loss

        self.perceptual_loss_sum = scalar_summary(
            "perceptual_loss", self.perceptual_loss)

        self.complete_loss = self.contextual_loss + \
            self.lambda_val * self.perceptual_loss

        self.complete_loss_sum = scalar_summary(
            "complete_loss_sum", self.complete_loss)

        self.grad_complete_loss = tf.gradients(self.complete_loss, self.z)

        self.grad_complete_loss_sum = scalar_summary(
            "grad_complete_loss", self.grad_complete_loss)

    def train(self, config):
        """Train DCGAN"""
        if config.dataset == 'mnist':
            data_X, data_y = self.load_mnist()
        else:
            data = glob(os.path.join(
                "./data", config.dataset, self.input_fname_pattern))
            # np.random.shuffle(data)

            # Xz-GAN
            ###################################################################
            data2 = glob(os.path.join(
                "./data", config.dataset2, self.input_fname_pattern))
            ###################################################################

        d_optim = tf.train.AdamOptimizer(config.learning_rate, beta1=config.beta1) \
            .minimize(self.d_loss, var_list=self.d_vars)
        g_optim = tf.train.AdamOptimizer(config.learning_rate, beta1=config.beta1) \
            .minimize(self.g_loss, var_list=self.g_vars)

        # Wasserstein-GAN
        #######################################################################
        # d_optim = tf.train.RMSPropOptimizer(learning_rate=5e-5).minimize(-self.d_loss, var_list=self.d_vars)
        # g_optim = tf.train.RMSPropOptimizer(learning_rate=5e-5).minimize(-self.g_loss, var_list=self.g_vars)
        # clip_d = [tf.assign(var, tf.clip_by_value(var, -0.01, 0.01)) for var in self.d_vars]
        #######################################################################

        try:
            tf.global_variables_initializer().run()
        except:
            # Fit for different APIs of Tensorflow
            tf.initialize_all_variables().run()

        self.g_sum = merge_summary(
            [self.z_sum, self.d__sum, self.G_sum, self.d_loss_fake_sum, self.g_loss_sum])
        self.d_sum = merge_summary(
            [self.z_sum, self.d_sum, self.d_loss_real_sum, self.d_loss_sum])
        self.inpaint_sum = merge_summary(
            [self.contextual_loss_sum, self.perceptual_loss_sum, self.complete_loss_sum, self.grad_complete_loss_sum])
        self.writer = SummaryWriter("./logs", self.sess.graph)

        sample_z = np.random.uniform(-1, 1, size=(self.sample_num, self.z_dim))

        if config.dataset == 'mnist':
            sample_inputs = data_X[0:self.sample_num]
            sample_labels = data_y[0:self.sample_num]
        else:
            sample_files = data[0:self.sample_num]
            sample = [
                get_image(sample_file,
                          input_height=self.input_height,
                          input_width=self.input_width,
                          resize_height=self.output_height,
                          resize_width=self.output_width,
                          is_crop=self.is_crop,
                          is_grayscale=self.is_grayscale) for sample_file in sample_files]
            # Xz-GAN
            ###################################################################
            sample_zs = data2[0:self.sample_num]
            sample_2 = [
                get_image(sample_file,
                          input_height=self.input_height,
                          input_width=self.input_width,
                          resize_height=self.output_height,
                          resize_width=self.output_width,
                          is_crop=self.is_crop,
                          is_grayscale=self.is_grayscale) for sample_file in sample_zs

            ]
            sample_noisy = copy.deepcopy(sample_2)
            sample_2 = [s.reshape([self.z_dim]) for s in sample_2]

            ###################################################################

            if (self.is_grayscale):
                sample_inputs = np.array(sample).astype(
                    np.float32)[:, :, :, None]
            else:
                sample_inputs = np.array(sample).astype(np.float32)
                # Xz-GAN
                ###############################################################
                sample_z = np.array(sample_2).astype(np.float32)
                ###############################################################

        counter = 1
        start_time = time.time()
        could_load, checkpoint_counter = self.load(self.checkpoint_dir)
        if could_load:
            counter = checkpoint_counter
            print(" [***] Load SUCCESS")
        else:
            print(" [!!!] Load failed...")

        for epoch in xrange(config.epoch):
            if config.dataset == 'mnist':
                batch_idxs = min(
                    len(data_X), config.train_size) // config.batch_size
            else:
                data = glob(os.path.join(
                    "./data", config.dataset, self.input_fname_pattern))
                # Xz-GAN
                ###############################################################
                data2 = glob(os.path.join(
                    "./data", config.dataset2, self.input_fname_pattern))
                ###############################################################

                batch_idxs = min(
                    len(data), config.train_size) // config.batch_size

            for idx in xrange(0, batch_idxs):
                if config.dataset == 'mnist':
                    batch_images = data_X[
                        idx * config.batch_size:(idx + 1) * config.batch_size]
                    batch_labels = data_y[
                        idx * config.batch_size:(idx + 1) * config.batch_size]
                else:
                    batch_files = data[
                        idx * config.batch_size:(idx + 1) * config.batch_size]
                    batch = [
                        get_image(batch_file,
                                  input_height=self.input_height,
                                  input_width=self.input_width,
                                  resize_height=self.output_height,
                                  resize_width=self.output_width,
                                  is_crop=self.is_crop,
                                  is_grayscale=self.is_grayscale) for batch_file in batch_files]
                    # Xz-GAN
                    ###########################################################
                    batch_zs = data2[0:self.sample_num]
                    batch_2 = [
                        get_image(batch_file,
                                  input_height=self.input_height,
                                  input_width=self.input_width,
                                  resize_height=self.output_height,
                                  resize_width=self.output_width,
                                  is_crop=self.is_crop,
                                  is_grayscale=self.is_grayscale).reshape([self.z_dim]) for batch_file in batch_zs]
                    ###########################################################

                    if (self.is_grayscale):
                        batch_images = np.array(batch).astype(
                            np.float32)[:, :, :, None]
                    else:
                        batch_images = np.array(batch).astype(np.float32)

                        # Xz-GAN
                        #######################################################
                        batch_z = np.array(batch_2).astype(np.float32)
                        #######################################################
                batch_z = np.random.uniform(-1, 1, [config.batch_size, self.z_dim]) \
                    .astype(np.float32)

                if config.dataset == 'mnist':
                    # Update D network
                    _, summary_str = self.sess.run([d_optim, self.d_sum],
                                                   feed_dict={
                        self.inputs: batch_images,
                        self.z: batch_z,
                        self.y: batch_labels,
                    })
                    self.writer.add_summary(summary_str, counter)

                    # Update G network
                    _, summary_str = self.sess.run([g_optim, self.g_sum],
                                                   feed_dict={
                        self.z: batch_z,
                        self.y: batch_labels,
                    })
                    self.writer.add_summary(summary_str, counter)

                    # Run g_optim twice to make sure that d_loss does not go to
                    # zero (different from paper)
                    _, summary_str = self.sess.run([g_optim, self.g_sum],
                                                   feed_dict={self.z: batch_z, self.y: batch_labels})
                    self.writer.add_summary(summary_str, counter)

                    errD_fake = self.d_loss_fake.eval({
                        self.z: batch_z,
                        self.y: batch_labels
                    })
                    errD_real = self.d_loss_real.eval({
                        self.inputs: batch_images,
                        self.y: batch_labels
                    })
                    errG = self.g_loss.eval({
                        self.z: batch_z,
                        self.y: batch_labels
                    })
                else:
                    # Update D network
                    _, summary_str = self.sess.run([d_optim, self.d_sum],
                                                   feed_dict={self.inputs: batch_images, self.z: batch_z})
                    self.writer.add_summary(summary_str, counter)

                    # Update G network
                    _, summary_str = self.sess.run([g_optim, self.g_sum],
                                                   feed_dict={self.z: batch_z})
                    self.writer.add_summary(summary_str, counter)

                    # Run g_optim twice to make sure that d_loss does not go to
                    # zero (different from paper)
                    _, summary_str = self.sess.run([g_optim, self.g_sum],
                                                   feed_dict={self.z: batch_z})
                    self.writer.add_summary(summary_str, counter)

                    ###########################################################
                    # Wasserstein-GAN
                    # _, summary_str, _ = self.sess.run([d_optim, self.d_sum, clip_d],
                    #                                  feed_dict={self.inputs: batch_images, self.z: batch_z})
                    # self.writer.add_summary(summary_str, counter)
                    #
                    # Update G network
                    # if idx % 5 == 0:
                    #     _, summary_str = self.sess.run([g_optim, self.g_sum],
                    #                                    feed_dict={self.z: batch_z})
                    #    self.writer.add_summary(summary_str, counter)
                    ###########################################################

                    errD_fake = self.d_loss_fake.eval({self.z: batch_z})
                    errD_real = self.d_loss_real.eval(
                        {self.inputs: batch_images})
                    errG = self.g_loss.eval({self.z: batch_z})

                counter += 1
                print("Epoch: [%2d] [%4d/%4d] time: %4.4f, d_loss: %.8f, g_loss: %.8f" % (epoch, idx, batch_idxs,
                                                                                          time.time() - start_time, errD_fake + errD_real, errG))

                if np.mod(counter, 100) == 1:
                    if config.dataset == 'mnist':
                        samples, d_loss, g_loss = self.sess.run(
                            [self.sampler, self.d_loss, self.g_loss],
                            feed_dict={
                                self.z: sample_z,
                                self.inputs: sample_inputs,
                                self.y: sample_labels,
                            }
                        )
                        manifold_h = int(np.ceil(np.sqrt(samples.shape[0])))
                        manifold_w = int(np.floor(np.sqrt(samples.shape[0])))
                        save_images(samples, [manifold_h, manifold_w],
                                    './{}/train_{:02d}_{:04d}.png'.format(config.sample_dir, epoch, idx))
                        print("[Sample] d_loss: %.8f, g_loss: %.8f" %
                              (d_loss, g_loss))
                    else:
                        try:
                            samples, d_loss, g_loss = self.sess.run(
                                [self.sampler, self.d_loss, self.g_loss],
                                feed_dict={
                                    self.z: sample_z,
                                    self.inputs: sample_inputs,
                                },
                            )
                            manifold_h = int(
                                np.ceil(np.sqrt(samples.shape[0])))
                            manifold_w = int(
                                np.floor(np.sqrt(samples.shape[0])))
                            save_images(samples, [manifold_h, manifold_w],
                                        './{}/train_{:02d}_{:04d}.png'.format(config.sample_dir, epoch, idx))
                            # Xz-GAN
                            ###################################################
                            sample_noisy = np.asarray(sample_noisy)
                            print(sample_noisy.shape)
                            save_images(sample_noisy, [
                                        manifold_h, manifold_w], './{}/noisy_{:02d}_{:04d}.png'.format(config.sample_dir, epoch, idx))
                            ###################################################
                            print("[Sample] d_loss: %.8f, g_loss: %.8f" %
                                  (d_loss, g_loss))
                        except:
                            print("one pic error!...")

                if np.mod(counter, 500) == 2:
                    self.save(config.checkpoint_dir, counter)
    # Class method for inpainting

    def inpaint(self, config):
        to_file_inpaint = []

        os.makedirs(os.path.join(config.outDir, 'hats_imgs'), exist_ok=True)
        os.makedirs(os.path.join(config.outDir, 'inpainted'), exist_ok=True)

        # tf.initialize_all_variables().run()
        tf.global_variables_initializer().run()

        isLoaded = self.load(self.checkpoint_dir)
        # Check if the model loaded
        assert(isLoaded[0])

        nImgs = len(config.imgs)

        # Deal with path list
        img_list = glob(os.path.join(config.imgs, '*.jpg'))

        batch_idxs = int(np.ceil(nImgs / self.batch_size))
        if config.maskType == 'random':
            fraction_masked = 0.2
            mask = np.ones(self.image_shape)
            mask[np.random.random(self.image_shape[:2]) <
                 fraction_masked] = 0.0
        elif config.maskType == 'center':
            scale = 0.25
            assert(scale <= 0.5)
            mask = np.ones(self.image_shape)
            sz = self.image_size
            l = int(self.image_size * scale)
            u = int(self.image_size * (1.0 - scale))
            mask[l:u, l:u, :] = 0.0
        elif config.maskType == 'left':
            mask = np.ones(self.image_shape)
            c = self.image_size // 2
            mask[:, :c, :] = 0.0
        elif config.maskType == 'full':
            mask = np.ones(self.image_shape)
        else:
            assert(False)

        for idx in xrange(0, batch_idxs):
            l = idx * self.batch_size
            u = min((idx + 1) * self.batch_size, nImgs)
            batchSz = u - l
            batch_files = img_list[l:u]
            batch = [get_image(batch_file, self.image_size, self.image_size, is_crop=self.is_crop)
                     for batch_file in batch_files]
            batch_images = np.array(batch).astype(np.float32)
            if batchSz < self.batch_size:
                print(batchSz)
                padSz = ((0, int(self.batch_size - batchSz)),
                         (0, 0), (0, 0), (0, 0))
                batch_images = np.pad(batch_images, padSz, 'constant')
                batch_images = batch_images.astype(np.float32)

            batch_mask = np.resize(mask, [self.batch_size] + self.image_shape)
            zhats = np.random.uniform(-1, 1,
                                      size=(self.batch_size, self.z_dim))
            v = 0

            nRows = np.ceil(batchSz / 8)
            nCols = 8
            save_images_inpaint(batch_images[:batchSz, :, :, :], [nRows, nCols],
                                os.path.join(config.outDir, 'before.png'))

            masked_images = np.multiply(batch_images, batch_mask)

            save_images_inpaint(masked_images[:batchSz, :, :, :], [nRows, nCols],
                                os.path.join(config.outDir, 'masked.png'))

            for i in xrange(config.nIter):
                fd = {
                    self.z: zhats,
                    self.mask: batch_mask,
                    self.inputs: batch_images,
                }
                run = [self.complete_loss, self.grad_complete_loss, self.G]
                loss, g, G_imgs = self.sess.run(run, feed_dict=fd)

                v_prev = np.copy(v)
                v = config.momentum * v - config.lr * g[0]
                zhats += -config.momentum * v_prev + (1 + config.momentum) * v
                zhats = np.clip(zhats, -1, 1)

                if i % 50 == 0:
                    inpaint_loss = np.mean(loss[0:batchSz])
                    print('Inpainting Epoch: {}           Inpainting Loss: {}'.format(
                        i, inpaint_loss))
                    to_file_inpaint.append({i: inpaint_loss})

                    imgName = os.path.join(config.outDir,
                                           'hats_imgs/{:04d}.png'.format(i))
                    nRows = np.ceil(batchSz / 8)
                    nCols = 8
                    save_images_inpaint(G_imgs[:batchSz, :, :, :],
                                        [nRows, nCols], imgName)

                    inv_masked_hat_images = np.multiply(
                        G_imgs, 1.0 - batch_mask)
                    completeed = masked_images + inv_masked_hat_images
                    imgName = os.path.join(config.outDir,
                                           'inpainted/{:04d}.png'.format(i))
                    save_images_inpaint(completeed[:batchSz, :, :, :], [
                        nRows, nCols], imgName)
        with open(os.path.join(config.outDir, 'Inpaint_Results.txt'), 'wb') as f:
            for data_item in to_file_inpaint:
                f.write(str(data_item) + '\n'.encode())

    def discriminator(self, image, y=None, reuse=False):
        with tf.variable_scope("discriminator") as scope:
            if reuse:
                scope.reuse_variables()

            if not self.y_dim:
                h0 = lrelu(conv2d(image, self.df_dim, name='d_h0_conv'))
                h1 = lrelu(self.d_bn1(
                    conv2d(h0, self.df_dim * 2, name='d_h1_conv')))
                h2 = lrelu(self.d_bn2(
                    conv2d(h1, self.df_dim * 4, name='d_h2_conv')))
                h3 = lrelu(self.d_bn3(
                    conv2d(h2, self.df_dim * 8, name='d_h3_conv')))
                h4 = linear(tf.reshape(
                    h3, [self.batch_size, -1]), 1, 'd_h3_lin')

                return tf.nn.sigmoid(h4), h4
            else:
                yb = tf.reshape(y, [self.batch_size, 1, 1, self.y_dim])
                x = conv_cond_concat(image, yb)

                h0 = lrelu(
                    conv2d(x, self.c_dim + self.y_dim, name='d_h0_conv'))
                h0 = conv_cond_concat(h0, yb)

                h1 = lrelu(self.d_bn1(
                    conv2d(h0, self.df_dim + self.y_dim, name='d_h1_conv')))
                h1 = tf.reshape(h1, [self.batch_size, -1])
                h1 = concat([h1, y], 1)

                h2 = lrelu(self.d_bn2(linear(h1, self.dfc_dim, 'd_h2_lin')))
                h2 = concat([h2, y], 1)

                h3 = linear(h2, 1, 'd_h3_lin')

                return tf.nn.sigmoid(h3), h3

    def generator(self, z, y=None):
        with tf.variable_scope("generator") as scope:
            if not self.y_dim:
                s_h, s_w = self.output_height, self.output_width
                s_h2, s_w2 = conv_out_size_same(
                    s_h, 2), conv_out_size_same(s_w, 2)
                s_h4, s_w4 = conv_out_size_same(
                    s_h2, 2), conv_out_size_same(s_w2, 2)
                s_h8, s_w8 = conv_out_size_same(
                    s_h4, 2), conv_out_size_same(s_w4, 2)
                s_h16, s_w16 = conv_out_size_same(
                    s_h8, 2), conv_out_size_same(s_w8, 2)

                # project `z` and reshape
                self.z_, self.h0_w, self.h0_b = linear(
                    z, self.gf_dim * 8 * s_h16 * s_w16, 'g_h0_lin', with_w=True)

                self.h0 = tf.reshape(
                    self.z_, [-1, s_h16, s_w16, self.gf_dim * 8])
                h0 = tf.nn.relu(self.g_bn0(self.h0))

                self.h1, self.h1_w, self.h1_b = deconv2d(
                    h0, [self.batch_size, s_h8, s_w8, self.gf_dim * 4], name='g_h1', with_w=True)
                h1 = tf.nn.relu(self.g_bn1(self.h1))

                h2, self.h2_w, self.h2_b = deconv2d(
                    h1, [self.batch_size, s_h4, s_w4, self.gf_dim * 2], name='g_h2', with_w=True)
                h2 = tf.nn.relu(self.g_bn2(h2))

                h3, self.h3_w, self.h3_b = deconv2d(
                    h2, [self.batch_size, s_h2, s_w2, self.gf_dim * 1], name='g_h3', with_w=True)
                h3 = tf.nn.relu(self.g_bn3(h3))

                h4, self.h4_w, self.h4_b = deconv2d(
                    h3, [self.batch_size, s_h, s_w, self.c_dim], name='g_h4', with_w=True)

                return tf.nn.tanh(h4)
            else:
                s_h, s_w = self.output_height, self.output_width
                s_h2, s_h4 = int(s_h / 2), int(s_h / 4)
                s_w2, s_w4 = int(s_w / 2), int(s_w / 4)

                # yb = tf.expand_dims(tf.expand_dims(y, 1),2)
                yb = tf.reshape(y, [self.batch_size, 1, 1, self.y_dim])
                z = concat([z, y], 1)

                h0 = tf.nn.relu(
                    self.g_bn0(linear(z, self.gfc_dim, 'g_h0_lin')))
                h0 = concat([h0, y], 1)

                h1 = tf.nn.relu(self.g_bn1(
                    linear(h0, self.gf_dim * 2 * s_h4 * s_w4, 'g_h1_lin')))
                h1 = tf.reshape(
                    h1, [self.batch_size, s_h4, s_w4, self.gf_dim * 2])

                h1 = conv_cond_concat(h1, yb)

                h2 = tf.nn.relu(self.g_bn2(deconv2d(h1,
                                                    [self.batch_size, s_h2, s_w2, self.gf_dim * 2], name='g_h2')))
                h2 = conv_cond_concat(h2, yb)

                return tf.nn.sigmoid(
                    deconv2d(h2, [self.batch_size, s_h, s_w, self.c_dim], name='g_h3'))

    def sampler(self, z, y=None):
        with tf.variable_scope("generator") as scope:
            scope.reuse_variables()

            if not self.y_dim:
                s_h, s_w = self.output_height, self.output_width
                s_h2, s_w2 = conv_out_size_same(
                    s_h, 2), conv_out_size_same(s_w, 2)
                s_h4, s_w4 = conv_out_size_same(
                    s_h2, 2), conv_out_size_same(s_w2, 2)
                s_h8, s_w8 = conv_out_size_same(
                    s_h4, 2), conv_out_size_same(s_w4, 2)
                s_h16, s_w16 = conv_out_size_same(
                    s_h8, 2), conv_out_size_same(s_w8, 2)

                # project `z` and reshape
                h0 = tf.reshape(
                    linear(z, self.gf_dim * 8 * s_h16 * s_w16, 'g_h0_lin'),
                    [-1, s_h16, s_w16, self.gf_dim * 8])
                h0 = tf.nn.relu(self.g_bn0(h0, train=False))

                h1 = deconv2d(h0, [self.batch_size, s_h8,
                                   s_w8, self.gf_dim * 4], name='g_h1')
                h1 = tf.nn.relu(self.g_bn1(h1, train=False))

                h2 = deconv2d(h1, [self.batch_size, s_h4,
                                   s_w4, self.gf_dim * 2], name='g_h2')
                h2 = tf.nn.relu(self.g_bn2(h2, train=False))

                h3 = deconv2d(h2, [self.batch_size, s_h2,
                                   s_w2, self.gf_dim * 1], name='g_h3')
                h3 = tf.nn.relu(self.g_bn3(h3, train=False))

                h4 = deconv2d(h3, [self.batch_size, s_h,
                                   s_w, self.c_dim], name='g_h4')

                return tf.nn.tanh(h4)
            else:
                s_h, s_w = self.output_height, self.output_width
                s_h2, s_h4 = int(s_h / 2), int(s_h / 4)
                s_w2, s_w4 = int(s_w / 2), int(s_w / 4)

                # yb = tf.reshape(y, [-1, 1, 1, self.y_dim])
                yb = tf.reshape(y, [self.batch_size, 1, 1, self.y_dim])
                z = concat([z, y], 1)

                h0 = tf.nn.relu(self.g_bn0(
                    linear(z, self.gfc_dim, 'g_h0_lin'), train=False))
                h0 = concat([h0, y], 1)

                h1 = tf.nn.relu(self.g_bn1(
                    linear(h0, self.gf_dim * 2 * s_h4 * s_w4, 'g_h1_lin'), train=False))
                h1 = tf.reshape(
                    h1, [self.batch_size, s_h4, s_w4, self.gf_dim * 2])
                h1 = conv_cond_concat(h1, yb)

                h2 = tf.nn.relu(self.g_bn2(
                    deconv2d(h1, [self.batch_size, s_h2, s_w2, self.gf_dim * 2], name='g_h2'), train=False))
                h2 = conv_cond_concat(h2, yb)

                return tf.nn.sigmoid(deconv2d(h2, [self.batch_size, s_h, s_w, self.c_dim], name='g_h3'))

    def load_mnist(self):
        data_dir = os.path.join("./data", self.dataset_name)

        fd = open(os.path.join(data_dir, 'train-images-idx3-ubyte'))
        loaded = np.fromfile(file=fd, dtype=np.uint8)
        trX = loaded[16:].reshape((60000, 28, 28, 1)).astype(np.float)

        fd = open(os.path.join(data_dir, 'train-labels-idx1-ubyte'))
        loaded = np.fromfile(file=fd, dtype=np.uint8)
        trY = loaded[8:].reshape((60000)).astype(np.float)

        fd = open(os.path.join(data_dir, 't10k-images-idx3-ubyte'))
        loaded = np.fromfile(file=fd, dtype=np.uint8)
        teX = loaded[16:].reshape((10000, 28, 28, 1)).astype(np.float)

        fd = open(os.path.join(data_dir, 't10k-labels-idx1-ubyte'))
        loaded = np.fromfile(file=fd, dtype=np.uint8)
        teY = loaded[8:].reshape((10000)).astype(np.float)

        trY = np.asarray(trY)
        teY = np.asarray(teY)

        X = np.concatenate((trX, teX), axis=0)
        y = np.concatenate((trY, teY), axis=0).astype(np.int)

        seed = 547
        np.random.seed(seed)
        np.random.shuffle(X)
        np.random.seed(seed)
        np.random.shuffle(y)

        y_vec = np.zeros((len(y), self.y_dim), dtype=np.float)
        for i, label in enumerate(y):
            y_vec[i, y[i]] = 1.0

        return X / 255., y_vec

    @property
    def model_dir(self):
        return "{}_{}_{}_{}".format(
            self.dataset_name, self.batch_size,
            self.output_height, self.output_width)

    def save(self, checkpoint_dir, step):
        model_name = "DCGAN.model"
        checkpoint_dir = os.path.join(checkpoint_dir, self.model_dir)

        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)

        self.saver.save(self.sess,
                        os.path.join(checkpoint_dir, model_name),
                        global_step=step)

    def load(self, checkpoint_dir):
        import re
        print(" [***] Reading checkpoints...")
        checkpoint_dir = os.path.join(checkpoint_dir, self.model_dir)

        ckpt = tf.train.get_checkpoint_state(checkpoint_dir)
        if ckpt and ckpt.model_checkpoint_path:
            ckpt_name = os.path.basename(ckpt.model_checkpoint_path)
            self.saver.restore(self.sess, os.path.join(
                checkpoint_dir, ckpt_name))
            counter = int(
                next(re.finditer("(\d+)(?!.*\d)", ckpt_name)).group(0))
            print(" [***] Success to read {}".format(ckpt_name))
            return True, counter
        else:
            print(" [*] Failed to find a checkpoint")
            return False, 0
