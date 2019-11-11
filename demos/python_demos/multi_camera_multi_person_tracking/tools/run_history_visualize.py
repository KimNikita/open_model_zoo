"""
 Copyright (c) 2019 Intel Corporation
 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at
      http://www.apache.org/licenses/LICENSE-2.0
 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""

import argparse
import copy
import glog as log
import json

import cv2 as cv
import numpy as np
import motmetrics as mm
from tqdm import tqdm

from utils.misc import check_pressed_keys
from utils.video import MulticamCapture
from utils.visualization import visualize_multicam_detections, plot_timeline
from tools.run_evaluate import read_gt_tracks, get_detections_from_tracks


def find_max_id(all_tracks):
    def find_max(tracks):
        max_id = 0
        for track in tracks:
            if track['id'] > max_id:
                max_id = track['id']
        return max_id

    output = []
    for cam_tracks in all_tracks:
        output.append(find_max(cam_tracks))
    return max(output)


def find_max_frame_num(tracks):
    output = [0 for _ in tracks]
    for i, cam_tracks in enumerate(tracks):
        for track in cam_tracks:
            output[i] = max(output[i], track['timestamps'][-1])
    return min(output)


def accumulate_mot_metrics(accs, gt_tracks, history):
    log.info('Accumulating MOT metrics...')
    last_frame = find_max_frame_num(gt_tracks)
    for time in tqdm(range(last_frame), 'Processing...'):
        active_detections = get_detections_from_tracks(history, time)
        gt_detections = get_detections_from_tracks(gt_tracks, time)

        for i, camera_gt_detections in enumerate(gt_detections):
            gt_boxes = []
            gt_labels = []
            for obj in camera_gt_detections:
                gt_boxes.append([obj.rect[0], obj.rect[1], obj.rect[2] - obj.rect[0], obj.rect[3] - obj.rect[1]])
                gt_labels.append(obj.label)
            ht_boxes = []
            ht_labels = []
            for obj in active_detections[i]:
                ht_boxes.append([obj.rect[0], obj.rect[1], obj.rect[2] - obj.rect[0], obj.rect[3] - obj.rect[1]])
                ht_labels.append(obj.label)
            distances = mm.distances.iou_matrix(np.array(gt_boxes), np.array(ht_boxes), max_iou=0.5)
            accs[i].update(gt_labels, ht_labels, distances)

    return accs


def match_gt_indices(gt_tracks, history, accs):
    log.info('Assigning GT IDs to IDs from history...')
    hist_max_id = find_max_id(history)
    gt_max_id = find_max_id(gt_tracks)
    assignment_matrix = np.zeros((gt_max_id + 1, hist_max_id + 1), dtype='int32')
    for acc in accs:
        for event in acc.events.values:
            if event[0] == 'MATCH':
                gt_id = int(event[1].split(' ')[1])
                hist_id = int(event[2].split(' ')[1])
                assignment_matrix[gt_id][hist_id] += 1
    assignment_indices = np.argsort(-assignment_matrix, axis=1)
    next_missed = -1
    for i in range(assignment_indices.shape[0]):
        if assignment_indices[i][0] == 0 and np.amax(assignment_matrix[i]) == 0:
            assignment_indices[i][0] = next_missed
            next_missed -= 1
    for i in range(len(gt_tracks)):
        used_ids = []
        for j in range(len(gt_tracks[i])):
            base_id = gt_tracks[i][j]['id']
            offset = 0
            while assignment_indices[gt_tracks[i][j]['id']][offset] in used_ids:
                offset += 1
            gt_tracks[i][j]['id'] = assignment_indices[gt_tracks[i][j]['id']][offset]
            used_ids.append(gt_tracks[i][j]['id'])
            log.info('Assigned GT ID: {} --> {}'.format(base_id, gt_tracks[i][j]['id']))
    return gt_tracks


def main():
    """Prepares data for the person recognition demo"""
    parser = argparse.ArgumentParser(description='Multi camera multi person \
                                                  tracking visualization demo script')
    parser.add_argument('--videos', type=str, nargs='+',
                        help='Input videos')
    parser.add_argument('--history_file', type=str, default='', required=True,
                        help='File with tracker history')
    parser.add_argument('--output_video', type=str, default='', required=False,
                        help='Output video file')
    parser.add_argument('--gt_files', type=str, nargs='+', required=False,
                        help='Files with ground truth annotation')
    parser.add_argument('--timeline', type=str, default='',
                        help='Plot and save timeline')
    parser.add_argument('--match_gt_ids', default=False, action='store_true',
                        help='Match GT ids to ids from history')
    parser.add_argument('--merge_outputs', default=False, action='store_true',
                        help='Merge GT and history tracks into one frame')

    args = parser.parse_args()

    capture = MulticamCapture(args.videos)
    with open(args.history_file) as hist_f:
        history = json.load(hist_f)

    assert len(history) == capture.get_num_sources()

    # Configure output video files
    output_video = None
    output_video_gt = None
    if len(args.output_video):
        divisor = capture.get_num_sources() if args.gt_files and not args.merge_outputs else 1
        video_output_size = (1920 // divisor, 1080)
        fourcc = cv.VideoWriter_fourcc(*'XVID')
        output_video = cv.VideoWriter(args.output_video, fourcc, 24.0, video_output_size)
        if args.gt_files and not args.merge_outputs:
            ext = args.output_video.split('.')[-1]
            output_path = args.output_video[:len(args.output_video) - len(ext) - 1] + '_gt.' + ext
            output_video_gt = cv.VideoWriter(output_path, fourcc, 24.0, video_output_size)

    # Create GT tracks if necessary
    if args.gt_files:
        assert len(args.gt_files) == capture.get_num_sources()
        gt_tracks, _ = read_gt_tracks(args.gt_files)
        accs = [mm.MOTAccumulator(auto_id=True) for _ in args.gt_files]
    else:
        gt_tracks = None

    # If we need for matching GT IDs, accumulate metrics
    if gt_tracks and args.match_gt_ids:
        accumulate_mot_metrics(accs, gt_tracks, history)
        match_gt_indices(gt_tracks, history, accs)
        metrics_accumulated = True
    else:
        metrics_accumulated = False

    # Process frames
    win_name = 'Multi camera tracking history visualizer'
    time = 0
    key = -1
    while True:
        key = check_pressed_keys(key)
        if key == 27:
            break
        has_frames, frames = capture.get_frames()
        if not has_frames:
            break

        if gt_tracks:
            gt_detections = get_detections_from_tracks(gt_tracks, time)
            vis_gt = visualize_multicam_detections(copy.deepcopy(frames), gt_detections)
        else:
            vis_gt = None

        active_detections = get_detections_from_tracks(history, time)
        vis = visualize_multicam_detections(frames, active_detections)

        if vis_gt is not None:
            if args.merge_outputs:
                vis = np.hstack([vis, vis_gt])
                cv.imshow(win_name, vis)
            else:
                cv.imshow('GT', vis_gt)
                cv.imshow(win_name, vis)
        else:
            cv.imshow(win_name, vis)
        time += 1

        if output_video:
            output_video.write(cv.resize(vis, video_output_size))
        if vis_gt is not None and output_video_gt is not None:
            output_video_gt.write(cv.resize(vis_gt, video_output_size))

    if len(args.timeline):
        for i in range(len(history)):
            log.info('Source_{}: drawing timeline...'.format(i))
            plot_timeline(i, time, history[i], save_path=args.timeline, name='SCT')
        if gt_tracks:
            for i in range(len(gt_tracks)):
                log.info('GT_{}: drawing timeline...'.format(i))
                plot_timeline(i, time, gt_tracks[i], save_path=args.timeline, name='GT')

    if gt_tracks:
        if not metrics_accumulated:
            accumulate_mot_metrics(accs, gt_tracks, history)
        mh = mm.metrics.create()
        summary = mh.compute_many(accs,
                                  metrics=mm.metrics.motchallenge_metrics,
                                  generate_overall=True,
                                  names=['video ' + str(i) for i in range(len(accs))])

        strsummary = mm.io.render_summary(summary,
                                          formatters=mh.formatters,
                                          namemap=mm.io.motchallenge_metric_names)
        print(strsummary)


if __name__ == '__main__':
    main()
