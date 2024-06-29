#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Jun 28 18:53:01 2024

@author: hibad
"""


# from __future__ import print_function
import open3d as o3d
from anomaly_detector import Anomaly_Detector
from ergodic_inspection.srv import PointCloudWithEntropy, PointCloudWithEntropyResponse
from sensor_msgs.msg import PointCloud2
import rospkg
rospack=rospkg.RosPack()
path = rospack.get_path("ergodic_inspection")

import rospy
def handle_add_two_ints(req):
     print("Requested Region ID: "+ req.regionID)
     return PointCloudWithEntropyResponse(0)
 
def pointcloud_server():
     rospy.init_node('reference_cloud_server')
     s = rospy.Service('add_two_ints', PointCloudWithEntropy, handle_add_two_ints)
     print("PointCloud server online")
     rospy.spin()
 
if __name__ == "__main__":
    mesh = o3d.io.read_triangle_mesh(path+"/resource/ballast.STL")
    box = mesh.get_axis_aligned_bounding_box()
    bound = [box.max_bound[0],box.max_bound[1], 0.7 ]
    box.max_bound = bound

    global detector
    detector = Anomaly_Detector(mesh, box,0.02)
    pointcloud_server()