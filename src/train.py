#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2017 Yasunori Kudo

from __future__ import print_function
import argparse
import multiprocessing
import numpy as np
import pickle
import time

import chainer
import chainer.functions as F
import chainer.links as L
from chainer import training
from chainer.training import extensions
from chainer import serializers

import os
import sys
sys.path.append(os.getcwd())
from models.net import ConvAE
from dataset import PoseDataset

from updater import Updater
from evaluator import Evaluator

def create_result_dir(dir):
    if not os.path.exists('results'):
        os.mkdir('results')
    if dir:
        result_dir = os.path.join('results', dir)
    else:
        result_dir = os.path.join(
            'results', time.strftime('%Y-%m-%d_%H-%M-%S'))
    if not os.path.exists(result_dir):
        os.mkdir(result_dir)
    return result_dir


def main():
    parser = argparse.ArgumentParser(description='chainer implementation of pix2pix')
    parser.add_argument('--n_class', type=int, default=20)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--resume', '-r', default='')

    parser.add_argument('--root', type=str, default='data/h3.6m')
    parser.add_argument('--l_latent', type=int, default=64)
    parser.add_argument('--l_seq', type=int, default=32)
    parser.add_argument('--gpu', '-g', type=int, default=0)
    parser.add_argument('--batchsize', '-b', type=int, default=16)
    parser.add_argument('--test_batchsize', type=int, default=32)
    parser.add_argument('--dir', type=str, default='')
    parser.add_argument('--epoch', '-e', type=int, default=200)
    parser.add_argument('--opt', type=str, default='Adam',
                        choices=['Adam', 'NesterovAG'])
    parser.add_argument('--lr', type=float, default=3e-4)
    parser.add_argument('--shift_interval', type=int, default=100)
    parser.add_argument('--bn', type=str, default='f', choices=['t', 'f'])
    args = parser.parse_args()
    args.dir = create_result_dir(args.dir)
    args.bn = args.bn == 't'
    with open(os.path.join(args.dir, 'options.pickle'), 'wb') as f:
        pickle.dump(args, f)

    print('GPU: {}'.format(args.gpu))
    print('# Minibatch-size: {}'.format(args.batchsize))
    print('# epoch: {}'.format(args.epoch))
    print('')

    # Set up a neural network to train
    gen = ConvAE(l_latent=args.l_latent, l_seq=args.l_seq, mode='generator', bn=args.bn)
    dis = ConvAE(l_latent=1, l_seq=args.l_seq, mode='discriminator', bn=False)
    if args.gpu >= 0:
        os.environ['CUDA_VISIBLE_DEVICES'] = str(args.gpu)
        chainer.cuda.get_device(0).use()  # Make a specified GPU current
        gen.to_gpu()
        dis.to_gpu()

    # Setup an optimizer
    def make_optimizer(model):
        if args.opt == 'Adam':
            optimizer = chainer.optimizers.Adam(alpha=2e-4, beta1=0.5)
            optimizer.setup(model)
            optimizer.add_hook(chainer.optimizer.WeightDecay(1e-5))
        elif args.opt == 'NesterovAG':
            optimizer = chainer.optimizers.NesterovAG(lr=args.lr, momentum=0.9)
            optimizer.setup(model)
            optimizer.add_hook(chainer.optimizer.WeightDecay(1e-4))
        else:
            raise NotImplementedError
        return optimizer
    opt_gen = make_optimizer(gen)
    opt_dis = make_optimizer(dis)

    train = PoseDataset(args.root, length=args.l_seq, train=True)
    test = PoseDataset(args.root, length=args.l_seq, train=False)
    multiprocessing.set_start_method('spawn')
    train_iter = chainer.iterators.MultiprocessIterator(train, args.batchsize)
    test_iter = chainer.iterators.MultiprocessIterator(
        test, args.test_batchsize, repeat=False, shuffle=False)

    # Set up a trainer
    updater = Updater(
        models=(gen, dis),
        iterator={'main': train_iter, 'test': test_iter},
        optimizer={'gen': opt_gen, 'dis': opt_dis},
        device=0)
    trainer = training.Trainer(updater, (args.epoch, 'epoch'), out=args.dir)

    log_interval = (1, 'epoch')
    snapshot_interval = (1, 'epoch')

    if args.opt == 'NesterovAG':
        trainer.extend(
            extensions.ExponentialShift('lr', 0.1, optimizer=opt_gen),
            trigger=(args.shift_interval, 'epoch'))
        trainer.extend(
            extensions.ExponentialShift('lr', 0.1, optimizer=opt_dis),
            trigger=(args.shift_interval, 'epoch'))
    trainer.extend(Evaluator(test_iter, {'gen': gen}, device=0),
                   trigger=log_interval)
    trainer.extend(extensions.snapshot_object(
        gen, 'gen_epoch_{.updater.epoch}.npz'), trigger=snapshot_interval)
    trainer.extend(extensions.snapshot_object(
        dis, 'dis_epoch_{.updater.epoch}.npz'), trigger=snapshot_interval)
    trainer.extend(extensions.LogReport(trigger=log_interval))
    trainer.extend(extensions.PrintReport([
        'epoch', 'iteration', 'gen/mse',
        'gen/loss', 'dis/loss', 'validation/gen/mse'
    ]), trigger=log_interval)
    trainer.extend(extensions.ProgressBar(update_interval=10))

    if args.resume:
        # Resume from a snapshot
        chainer.serializers.load_npz(args.resume, trainer)

    # Run the training
    trainer.run()

if __name__ == '__main__':
    main()
