#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Nov  7 18:40:13 2023

@author: barc
"""
import rospy 
import rospkg
from sensor_msgs.msg import Image, CameraInfo, PointCloud2
from nav_msgs.msg import Odometry
from cv_bridge import CvBridge
import cv2
import numpy as np
from numpy.linalg import inv
import message_filters
import tf
from visualization_msgs.msg import Marker, MarkerArray
from geometry_msgs.msg import Pose 
np.float = np.float64 
import ros_numpy
import threading
from apriltag_EKF_SE3 import EKF


rospack=rospkg.RosPack()
np.set_printoptions(precision=2)

def get_camera_to_robot_tf():
    listener=tf.TransformListener()
    listener.waitForTransform('/base_footprint','/camera_rgb_optical_frame',rospy.Time(), rospy.Duration(4.0))
    (trans, rot) = listener.lookupTransform('/base_footprint', '/camera_rgb_optical_frame', rospy.Time(0))
    T_c_to_r=listener.fromTranslationRotation(trans, rot)
    return T_c_to_r

def msg2pc(msg):
    pc=ros_numpy.numpify(msg)
    m,n = pc['x'].shape
    depth = pc['z']
    x=pc['x'].reshape(-1)
    points=np.zeros((len(x),3))
    points[:,0]=x
    points[:,1]=pc['y'].reshape(-1)
    points[:,2]=pc['z'].reshape(-1)
    pc=ros_numpy.point_cloud2.split_rgb_field(pc)
    img = np.zeros((m,n,3))
    img[:,:,0] = pc['r']
    img[:,:,1] = pc['g']
    img[:,:,2] = pc['b']


    rgb=np.zeros((len(x),3))
    rgb[:,0]=pc['r'].reshape(-1)
    rgb[:,1]=pc['g'].reshape(-1)
    rgb[:,2]=pc['b'].reshape(-1)
    # p=o3d.geometry.PointCloud()
    # p.points=o3d.utility.Vector3dVector(points)
    # p.colors=o3d.utility.Vector3dVector(np.asarray(rgb/255))
    p = {"points": points, "colors": np.asarray(rgb/255)}
    return p, depth, img.astype('uint8')    

def draw_frame(img, tag, K):
    img=cv2.circle(img, (int(tag["xp"]), int(tag["yp"])), 5, (0, 0, 255), -1)
    M=tag["M"].copy()
    
    x_axis=K@M[0:3,:]@np.array([0.06,0,0,1])
    x_axis=x_axis/(x_axis[2])
    
    img=cv2.arrowedLine(img, (int(tag["xp"]), int(tag["yp"])), (int(x_axis[0]), int(x_axis[1])), 
                                     (0,0,255), 5)  
    return img


class EKF_Wrapper:
    def __init__(self, node_id):
        self.bridge = CvBridge()

        T_c_to_r = get_camera_to_robot_tf()
        self.lock=threading.Lock()
        camera_info = self.get_message("/camera/rgb/camera_info", CameraInfo)
        K = np.reshape(camera_info.K, (3,3))
        self.marker_pub = rospy.Publisher("/apriltags", Marker, queue_size = 2)
        self.image_pub = rospy.Publisher("/camera/rgb/rgb_detected", Image, queue_size = 2)
        
       
        odom=rospy.wait_for_message("/odom",Odometry)
        R=tf.transformations.quaternion_matrix([odom.pose.pose.orientation.x,
                                                   odom.pose.pose.orientation.y,
                                                   odom.pose.pose.orientation.z,
                                                   odom.pose.pose.orientation.w])[0:3,0:3]
        M=np.eye(4)
        M[0:3,0:3] = R
        M[0:3,3]=[odom.pose.pose.position.x,
                  odom.pose.pose.position.y,
                  odom.pose.pose.position.z]
        
        self.ekf = EKF(node_id, T_c_to_r, K, M)

        self.reset(node_id)


       # rospy.Subscriber("/robot_pose_ekf/odom_combined", PoseWithCovarianceStamped, self.odom_callback)
        rospy.Subscriber("/odom", Odometry, self.odom_callback)
        rgbsub=message_filters.Subscriber("/camera/rgb/image_rect_color", Image)
        depthsub=message_filters.Subscriber("/camera/depth_registered/image_raw", Image)

        ts = message_filters.ApproximateTimeSynchronizer([rgbsub, depthsub], 10, 0.1, allow_headerless=True)
        ts.registerCallback(self.camera_callback)

        
    def reset(self, node_id):
        print("reseting EKF")
        with self.lock:
            pc_info = self.get_point_cloud()
            self.id = node_id
            self.ekf.reset(node_id, pc_info)
        print("EKF initialized") 
    
    def get_point_cloud(self):
        pc_msg=rospy.wait_for_message("/depth_registered/points",PointCloud2)
        pc_info = msg2pc(pc_msg)
        return pc_info
    
    def get_message(self, topic, msgtype):
        	try:
        		data=rospy.wait_for_message(topic,msgtype)
        		return data 
        	except rospy.ServiceException as e:
        		print("Service all failed: %s"%e)

    def odom_callback(self, data):
        with self.lock:
            R=tf.transformations.quaternion_matrix([data.pose.pose.orientation.x,
                                                        data.pose.pose.orientation.y,
                                                        data.pose.pose.orientation.z,
                                                        data.pose.pose.orientation.w])[0:3,0:3]

            odom = np.eye(4)
            odom[0:3,0:3] = R
            odom[0:3,3]=[data.pose.pose.position.x,
                              data.pose.pose.position.y,
                              data.pose.pose.position.z]
            
            Rv = np.eye(6)
            Rv[0,0] = data.twist.twist.linear.x**2
            Rv[1,1] = data.twist.twist.linear.y**2
            Rv[2,2] =  data.twist.twist.linear.z**2 
            Rv[3,3] =  data.twist.twist.angular.x**2 
            Rv[4,4] =  data.twist.twist.angular.y**2
            Rv[5,5] =  data.twist.twist.angular.z**2
            self.ekf.motion_update(odom, Rv)
               
    def camera_callback(self, rgb_msg, depth_msg):
        with self.lock:
            rgb = self.bridge.imgmsg_to_cv2(rgb_msg,"bgr8")
            depth = self.bridge.imgmsg_to_cv2(depth_msg,"32FC1")
            self.ekf.camera_update(self, rgb, depth)
            
def get_pose_marker(tags, mu):
    markers=[]
    for tag_id, idx in tags.items():
        marker=Marker()
        M=mu[idx]
        p=Pose()
        p.position.x = M[0,3]
        p.position.y = M[1,3]
        p.position.z = M[2,3]
        q=tf.transformations.quaternion_from_matrix(M)
        p.orientation.x = q[0]
        p.orientation.y = q[1]
        p.orientation.z = q[2]
        p.orientation.w = q[3]

    
        marker = Marker()
        marker.type = 0
        marker.id = tag_id
        
        marker.header.frame_id = "map"
        marker.header.stamp = rospy.Time.now()
        
        marker.pose.orientation.x=0
        marker.pose.orientation.y=0
        marker.pose.orientation.z=0
        marker.pose.orientation.w=1
        
        
        marker.scale.x = 0.5
        marker.scale.y = 0.05
        marker.scale.z = 0.05
        
        # Set the color
        marker.color.r = 1.0
        marker.color.g = 0.0
        marker.color.b = 0.0
        marker.color.a = 1.0
        
        marker.pose = p
        markers.append(marker)
    markerArray=MarkerArray()
    markerArray.markers=markers
    return markerArray

if __name__ == "__main__":
    rospy.init_node('EKF',anonymous=False)
    pc_pub=rospy.Publisher("/pc_rgb", PointCloud2, queue_size = 2)
    factor_graph_marker_pub = rospy.Publisher("/factor_graph", MarkerArray, queue_size = 2)

    wrapper = EKF_Wrapper(0)
    br = tf.TransformBroadcaster()
    rate = rospy.Rate(30) # 10hz
    while not rospy.is_shutdown():
        # pc_pub.publish(ekf.cloud)
        markers=get_pose_marker(wrapper.ekf.landmarks, wrapper.ekf.mu)
        factor_graph_marker_pub.publish(markers)
        M = wrapper.ekf.mu[0]
        M = M@inv(wrapper.kf.odom_prev)
        br.sendTransform((M[0,3], M[1,3] , M[2,3]),
                        tf.transformations.quaternion_from_matrix(M),
                        rospy.Time.now(),
                        "odom",
                        "map")
     
        print("here")
        rate.sleep()
