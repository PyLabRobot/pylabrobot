pylabrobot.liquid\_handling.backends.hamilton.vantage.Vantage
=============================================================

.. currentmodule:: pylabrobot.liquid_handling.backends.hamilton.vantage

.. autoclass:: Vantage

   
   
   .. rubric:: Attributes

   .. autosummary::
      :toctree: .
   
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.module_id_length
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.num_channels
   
   

   
   
   .. rubric:: Methods

   .. autosummary::
      :toctree: .
   
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.__init__
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.arm_pre_initialize
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.arm_request_instrument_initialization_status
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.aspirate
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.aspirate96
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.assigned_resource_callback
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.calculates_check_sums_and_compares_them_with_the_value_saved_in_flash_eprom
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.check_fw_string_error
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_aspiration_of_liquid
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_dispensing_of_liquid
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_empty_washed_tips
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_initialize
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_move_to_defined_position
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_query_tip_presence
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_request_initialization_status
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_request_position
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_request_tadm_error_status
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_search_for_teach_in_signal_in_x_direction
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_set_any_parameter
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_tip_discard
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_tip_pick_up
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.core96_wash_tips
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.define_tip_needle
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.deserialize
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.discard_core_gripper_tool
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.disco_mode
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.dispense
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.dispense96
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.dispense_on_fly
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.drop_tips
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.drop_tips96
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.expose_channel_n
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.get_available_devices
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.get_id_from_fw_response
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.get_or_assign_tip_type_index
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.get_ttti
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.grip_plate
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_expose_channel_n
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_get_parking_status
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_grip_plate
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_initialize
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_move_to_defined_position
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_park
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_prepare_gripper_orientation
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_put_plate
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_query_tip_presence
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_release_object
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_request_access_range
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_request_actual_angular_dimensions
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_request_configuration
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_request_initialization_status
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_request_position
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_search_for_teach_in_signal_in_x_direction
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.ipg_set_any_parameter_within_this_module
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.list_available_devices
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.loading_cover_initialize
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.loading_cover_request_initialization_status
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.move_channel_x
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.move_channel_y
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.move_channel_z
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.move_picked_up_resource
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.move_resource
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.move_to_defined_position
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.move_to_position
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.nano_pulse_dispense
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.pick_up_resource
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.pick_up_tips
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.pick_up_tips96
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.pip_aspirate
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.pip_dispense
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.pip_initialize
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.pip_request_initialization_status
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.pip_tip_discard
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.pip_tip_pick_up
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.position_all_channels_in_y_direction
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.position_all_channels_in_z_direction
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.position_single_channel_in_y_direction
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.position_single_channel_in_z_direction
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.prepare_for_manual_channel_operation
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.put_plate
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.query_tip_presence
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.read
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.release_object
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.release_picked_up_resource
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.request_channel_dispense_on_fly_status
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.request_height_of_last_lld
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.request_y_position_of_channel_n
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.request_y_positions_of_all_channels
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.request_z_position_of_channel_n
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.request_z_positions_of_all_channels
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.russian_roulette
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.search_for_teach_in_signal_in_x_direction
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.send_command
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.send_raw_command
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.serialize
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.set_any_parameter_within_this_module
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.set_led_color
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.set_loading_cover
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.set_minimum_traversal_height
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.setup
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.simultaneous_aspiration_dispensation_of_liquid
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.stop
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.teach_rack_using_channel_n
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.unassigned_resource_callback
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.wash_tips
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.write
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.x_arm_initialize
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.x_arm_move_arm_relatively_in_x
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.x_arm_move_to_x_position
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.x_arm_move_to_x_position_with_all_attached_components_in_z_safety_position
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.x_arm_request_arm_x_position
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.x_arm_request_error_code
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.x_arm_request_x_drive_recorded_data
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.x_arm_search_x_for_teach_signal
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.x_arm_send_message_to_motion_controller
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.x_arm_set_any_parameter_within_this_module
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.x_arm_set_x_drive_angle_of_alignment
      ~pylabrobot.liquid_handling.backends.hamilton.vantage.Vantage.x_arm_turn_x_drive_off
   
   