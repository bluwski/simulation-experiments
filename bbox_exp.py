import math
import os
import time
import matplotlib.patches as pch
import matplotlib.pyplot as plt
import torch
import psutil  # Import the psutil library for memory usage calculation

from iou import IoU_Cal
from optimize import minimize

red = 'orangered'
orange = 'darkorange'
yellow = 'gold'
green = 'greenyellow'
cyan = 'aqua'
blue = 'deepskyblue'
purple = 'mediumpurple'
pink = 'violet'

plt.rcParams['figure.dpi'] = 150

COLORS = [purple, blue, green, yellow, orange]


# ... (Rest of your code)

def run_once(func):
    def handler(*args, **kwargs):
        while True:
            try:
                return func(*args, **kwargs)
            except Exception as reason:
                print(reason)
                time.sleep(20)
                continue

    return handler


def xywh_to_ltrb(attr):
    attr[..., :2] -= attr[..., 2: 4] / 2
    attr[..., 2: 4] += attr[..., :2]
    return attr


def scatter_circle(n, radius, dot=[0., 0.], alpha=3):
    ''' Generate scatter uniformly in a circular area'''
    rho = torch.log(torch.rand(n) * (math.exp(alpha) - 1) + 1) / alpha * radius
    theta = torch.rand(n) * math.tau
    x = torch.cos(theta) * rho + dot[0]
    y = torch.sin(theta) * rho + dot[1]
    return x, y


@run_once
def simulate_exp(loss_fcn, lr=.01, max_iter=120,
                 plot_points=False,
                 n_points=None,
                 major_cases=True,
                 target_boxes_area=1 / 32,
                 anchor_boxes_areas=[1 / 32, 1 / 24, 3 / 64, 1 / 16, 1 / 12, 3 / 32, 1 / 8],
                 aspect_ratios=[1 / 4, 1 / 3, 1 / 2, 1, 2, 3, 4]):
    ''' loss_fcn: BBR losses used in simulation experiment
        plot_points: Display the anchor point distribution map
        n_points: The number of randomly generated anchors
        major_cases: Only the main cases in the regression process are addressed
        target_boxes_areas: The area of the target box
        anchor_boxes_areas: Area of anchor boxes
        aspect_ratios: Aspect ratio of bounding boxes'''
    IoU = IoU_Cal.IoU
    IoU_Cal.iou_mean = 1.
    aspect_ratios = torch.tensor(aspect_ratios)
    anchor_boxes_areas = torch.tensor(anchor_boxes_areas)
    # The distribution pattern of the regression cases
    points_radius = 0.1 if major_cases else 0.5
    max_iter = max_iter // 2 if major_cases else max_iter
    n_points = n_points if n_points else int(2e4 * points_radius ** 2)
    # The coordinates need to be transformed to [0, 1]
    x, y = scatter_circle(n_points, radius=points_radius, dot=[.5, .5])
    # 7*7 anchor boxes are generated at each anchor point
    width = (anchor_boxes_areas[:, None] / aspect_ratios).sqrt()
    height = aspect_ratios * width
    width, height = map(torch.flatten, [width, height])
    # Splice and get all anchor boxes
    xy = torch.stack([x, y], dim=-1)
    wh = torch.stack([width, height], dim=-1)
    anchor = torch.cat([xy[:, None].repeat(1, len(width), 1),
                        wh[None].repeat(len(x), 1, 1)], dim=-1)[..., None, :]
    # Get the target box
    target_w = (target_boxes_area / aspect_ratios).sqrt()
    target_h = target_w * aspect_ratios
    target = torch.cat([torch.full([len(aspect_ratios), 2], 0.5),
                        target_w[:, None], target_h[:, None]], dim=-1)
    anchor, target = map(xywh_to_ltrb, [anchor, target])
    anchor = anchor.repeat(1, 1, len(aspect_ratios), 1)
    # Draw the anchor point distribution map
    if plot_points:
        fig = plt.subplot()
        plt.scatter(x, y, s=0.3, color=blue)
        for axis in 'xy': getattr(plt, f'{axis}lim')([-0.05, 1.05])
        for l, t, r, b in target:
            rect = pch.Rectangle((l, t), (r - l), (b - t), alpha=0.2, facecolor=purple)
            fig.add_patch(rect)
        plt.show()
    # Construct the loss function and solve it using the function <minimize>
    result, _, log = minimize(anchor.detach(), lambda x: loss_fcn(x, target).mean(), lr=lr,
                              eval_fcn=lambda x: IoU(x.detach(), target).mean(),
                              max_iter=max_iter, prefix=loss_fcn.__name__)
    loss = IoU(result, target).mean(dim=(1, 2))
    loss_fcn = loss_fcn.__name__
    print(f'{loss_fcn}: Mean IoU = {1 - loss.mean():.3f}, Min IoU = {1 - loss.max():.3f}')
    # Draw the heat map of the IoU loss
    # fig = plt.subplot(projection='3d')
    # plt.title(loss_fcn)
    # fig.set_xlabel('x')
    # fig.set_ylabel('y')
    # fig.set_zlabel('IoU')
    # fig.view_init(40, 30)
    # fig.scatter(x, y, loss, cmap=plt.get_cmap('rainbow'), c=(loss - loss.min()) / (loss.max() - loss.min()))
    return {loss_fcn: log}

def plot_loss(fcn_list, **simlate_kwargs):
    ''' Draw the IoU loss curve
        fcn_list: List of loss functions participating in the test
        simlate_kwargs: The keyword argument of function <simulate_exp>'''
    assert len(COLORS) >= len(fcn_list), 'Insufficient amount of color provided'
    log_dict = {}
    for fcn in fcn_list:
        log_dict.update(simulate_exp(fcn, **simlate_kwargs))
    fig = plt.subplot()
    for key in 'right', 'top':
        fig.spines[key].set_color('None')
    plt.xlabel('Epochs')
    plt.ylabel('IoU')
    for color, fcn in zip(COLORS, log_dict):
        log = log_dict[fcn]
        x = torch.arange(1, len(log) + 1)
        plt.plot(x, log, label=fcn, color=color)
    plt.legend(frameon=False)
    plt.show()

def visualize_track(fcn_and_epoch: dict, lr=.01, colors=COLORS):
    ''' Visual bounding box regression
        fcn_and_epoch: {fcn: epoch ...}'''
    assert len(colors) >= len(fcn_and_epoch), 'Insufficient amount of color provided'
    IoU = IoU_Cal.IoU
    anchor = xywh_to_ltrb(torch.tensor([[.7, .7, .2, .4],
                                        [.5, .8, .6, .1]]))
    target = xywh_to_ltrb(torch.tensor([[.2, .2, .05, .1],
                                        [.5, .1, .05, .05]]))
    # Fixed the format of key-value pairs
    for fcn in fcn_and_epoch:
        epoch = fcn_and_epoch[fcn]
        if isinstance(epoch, int): fcn_and_epoch[fcn] = [epoch] * 2
        assert len(fcn_and_epoch[fcn]) == 2
    # The BBR is simulated using a gradient descent algorithm
    for i in range(2):
        fig = plt.subplot(1, 2, i + 1)
        for f in [plt.xlim, plt.ylim]: f([0, 1])
        for f in [plt.xticks, plt.yticks]: f([])
        # for loc in ['top', 'bottom', 'left', 'right']: fig.spines[loc].set_color('None')
        # Draw anchor boxes and target boxes
        anc = pch.Rectangle(anchor[i][:2], *(anchor[i][2:] - anchor[i][:2]),
                            edgecolor=green, fill=False, label='Bbox')
        anc.set_zorder(1)
        tar = pch.Rectangle(target[i][:2], *(target[i][2:] - target[i][:2]),
                            edgecolor=red, fill=False, label='GT')
        tar.set_zorder(1)
        for p in [anc, tar]: fig.add_patch(p)
        # Draws the anchor box in the optimization
        for j, (color, fcn) in enumerate(zip(colors, fcn_and_epoch)):
            epoch = fcn_and_epoch[fcn][i]
            result = minimize(anchor[i].clone(), lambda x: fcn(x, target[i]), lr=lr,
                              eval_fcn=lambda x: IoU(x.detach(), target[i]),
                              max_iter=epoch, patience=None,
                              prefix=fcn.__name__, title=not any([i, j]))[0]
            res = pch.Rectangle(result[:2], *(result[2:] - result[:2]),
                                facecolor=color, alpha=0.5, label=f'{fcn.__name__} {epoch} epochs')
            res.set_zorder(-j)
            fig.add_patch(res)
        plt.legend(frameon=False)
        plt.tight_layout()
    plt.show()

def plot_gain(gamma=[2.5, 1.9, 1.6, 1.4], delta=[2, 3, 4, 5],
              colors=[pink, blue, yellow, orange]):
    fig = plt.subplot()
    for key in 'right', 'top':
        fig.spines[key].set_color('None')
    for key in 'left', 'bottom':
        fig.spines[key].set_position(('data', 0))
    # The outlier degree of bounding box
    beta = torch.linspace(0, 8, 100)
    for g, d, c in zip(gamma, delta, colors):
        alpha = d * torch.pow(g, beta - d)
        plt.plot(beta, beta / alpha, color=c, label=f'α={g}  δ={d}')
    plt.plot(beta, torch.ones_like(beta), color='gray', linestyle='--', alpha=0.7)
    # Sets the format of the axes
    plt.xlabel('outlier degree')
    plt.xticks(*[list(range(0, 9, 2)) * 2])
    plt.ylabel('gradient gain')
    plt.yticks(*[[0.5, 1, 1.5] * 2])
    plt.ylim([0, 1.8])
    plt.legend(frameon=False)
    plt.show()

if __name__ == '__main__':
    # Initialize psutil process
    process = psutil.Process(os.getpid())
    
    f = IoU_Cal
    f.monotonous = None
    f.momentum = 0.06

    # 0: Plot the bounding box regression loss in the simulation experiment
    # 1: Visualize regression cases of simulation experiment
    # 2: Visualize the trajectory of regression cases under the effect of WIoU loss and SIoU loss
    # 3: Plot the relationship between the gradient multiplier r and the outlier degree β
    plt.rcParams['figure.figsize'] = [4.0, 3.0]
    command = [lambda: plot_loss([f.Proposed, f.GIoU, f.CIoU], n_points=500, major_cases=True),
               lambda: simulate_exp(f.CIoU, plot_points=True, major_cases=True),
               lambda: simulate_exp(f.Proposed, plot_points=True, major_cases=True),
               lambda: simulate_exp(f.GIoU, plot_points=True, major_cases=True),
               lambda: visualize_track({f.Proposed: 240, f.CIoU: 260, f.GIoU:550}, #
                                       colors=[cyan, pink, yellow])] 
               #lambda: plot_gain()]

    # Calculate and print initial memory usage
    initial_memory = process.memory_info().rss / (1024 ** 2)
    print(f'Initial memory usage: {initial_memory} MB')

    for i, cmd in enumerate(command):
        # Calculate memory usage before running the command
        memory_before = process.memory_info().rss / (1024 ** 2)
        
        # Run the command
        cmd()
        
        # Calculate memory usage after running the command
        memory_after = process.memory_info().rss / (1024 ** 2)
        
        print(f'Memory usage before command {i + 1}: {memory_before} MB')
        print(f'Memory usage after command {i + 1}: {memory_after} MB')
        print(f'Memory usage increase for command {i + 1}: {memory_after - memory_before} MB')
